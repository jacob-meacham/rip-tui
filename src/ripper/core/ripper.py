"""Ripping engine with progress tracking and cancellation."""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ripper.config.settings import Settings
from ripper.core.disc import Title
from ripper.core.scanner import MakeMKVNotFoundError

logger = logging.getLogger(__name__)

_PROGRESS_DEBUG_ENV = "RIPPER_PROGRESS_DEBUG"
_PROGRESS_DEBUG_FILE_ENV = "RIPPER_PROGRESS_DEBUG_FILE"


@dataclass
class RipProgress:
    """Progress update from a rip operation."""

    title_id: int
    title_name: str
    percent: float  # 0.0 - 100.0
    current_bytes: int
    total_bytes: int
    eta_seconds: int | None = None
    bytes_per_second: float | None = None


ProgressCallback = Callable[[RipProgress], None]

# makemkvcon progress line pattern: PRGV:current,total,max
PROGRESS_RE = re.compile(r"^PRGV:(\d+),(\d+),(\d+)")
# Current title being processed: PRGC:id,code,"name"
CURRENT_TITLE_RE = re.compile(r'^PRGC:(\d+),\d+,"(.*)"')
# Human-readable progress title: PRGT:cur,total,"message"
PROGRESS_TITLE_RE = re.compile(r'^PRGT:\d+,\d+,"(.*)"')
# Human-readable fallback from non-robot output:
# Current progress - 8%  , Total progress - 7%
HUMAN_PROGRESS_RE = re.compile(
    r"^Current progress - (\d+)%\s*,\s*Total progress - (\d+)%$"
)
# Human-readable status text:
# Current action: Saving to MKV file
# Current operation: Saving all titles to MKV files
HUMAN_ACTION_RE = re.compile(r"^Current action:\s*(.+)$")
HUMAN_OPERATION_RE = re.compile(r"^Current operation:\s*(.+)$")


class RipCancelledError(Exception):
    """Raised when a rip is cancelled by the user."""


# Global reference to the active makemkvcon process for cancellation
_active_process: subprocess.Popen | None = None
_process_lock = threading.Lock()


class _ProgressDebugHarness:
    """Writes detailed parser and callback events to a JSONL trace."""

    def __init__(self, path: Path, stream: TextIO) -> None:
        self.path = path
        self._stream = stream
        self._start_time = time.monotonic()
        self._seq = 0
        self._write_failed = False

    @classmethod
    def from_environment(
        cls,
        cmd: list[str],
        current_title: Title | None,
    ) -> "_ProgressDebugHarness | None":
        if not _env_flag_enabled(_PROGRESS_DEBUG_ENV):
            return None

        path = _resolve_progress_debug_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a", encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Could not create progress debug trace at %s: %s",
                path,
                exc,
            )
            return None

        harness = cls(path, stream)
        harness.record(
            "session_start",
            cmd=cmd,
            current_title=_title_to_dict(current_title),
        )
        logger.warning("Progress debug trace enabled: %s", path)
        return harness

    def record(self, event: str, **payload: object) -> None:
        """Write one JSON event; degrade gracefully on write failures."""
        if self._write_failed:
            return

        self._seq += 1
        row = {
            "seq": self._seq,
            "event": event,
            "elapsed_ms": int(
                (time.monotonic() - self._start_time) * 1000
            ),
            **payload,
        }
        try:
            self._stream.write(json.dumps(row, ensure_ascii=True))
            self._stream.write("\n")
            self._stream.flush()
        except OSError:
            self._write_failed = True
            logger.warning(
                "Progress debug trace write failed for %s",
                self.path,
            )

    def close(self) -> None:
        try:
            self._stream.close()
        except OSError:
            pass


def cancel_active_rip() -> None:
    """Kill the currently running makemkvcon process, if any."""
    with _process_lock:
        if _active_process and _active_process.poll() is None:
            logger.info("Cancelling active rip...")
            _active_process.terminate()
            try:
                _active_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _active_process.kill()


def rip_titles(
    titles: list[Title],
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> list[Path]:
    """Rip selected titles to output directory.

    Returns list of output MKV file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ripped_files: list[Path] = []

    for title in titles:
        logger.info("Ripping title %d: %s", title.id, title.name)
        output_file = _rip_single_title(
            title, output_dir, settings, on_progress
        )
        if output_file:
            ripped_files.append(output_file)

    logger.info("Rip complete: %d file(s)", len(ripped_files))
    return ripped_files


def rip_all_titles(
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> list[Path]:
    """Rip all titles from disc to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    source = f"dev:{settings.device}"
    cmd = [
        "makemkvcon",
        "mkv",
        source,
        "all",
        str(output_dir),
        f"--minlength={settings.min_extra_length}",
        "--progress=-same",
    ]

    logger.info("Ripping all titles to %s", output_dir)
    _run_makemkv(cmd, on_progress)

    ripped = sorted(output_dir.glob("*.mkv"))
    logger.info("Rip complete: %d file(s)", len(ripped))
    return ripped


def backup_disc(
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Backup entire disc to a decrypted BDMV structure.

    Returns the output directory containing the BDMV tree.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    source = f"disc:{settings.device}"
    cmd = [
        "makemkvcon",
        "backup",
        "--decrypt",
        source,
        str(output_dir),
        "--progress=-same",
    ]

    logger.info("Backing up disc to %s", output_dir)
    _run_makemkv(cmd, on_progress)
    return output_dir


def remux_all_from_backup(
    backup_dir: Path,
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> list[Path]:
    """Remux all titles from a backup to MKV files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    source = f"file:{backup_dir}"
    cmd = [
        "makemkvcon",
        "mkv",
        source,
        "all",
        str(output_dir),
        f"--minlength={settings.min_extra_length}",
        "--progress=-same",
    ]

    logger.info("Remuxing all titles from backup to %s", output_dir)
    _run_makemkv(cmd, on_progress)

    ripped = sorted(output_dir.glob("*.mkv"))
    logger.info("Remux complete: %d file(s)", len(ripped))
    return ripped


def remux_titles_from_backup(
    backup_dir: Path,
    titles: list[Title],
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> list[Path]:
    """Remux specific titles from a backup to MKV files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    source = f"file:{backup_dir}"
    for title in titles:
        cmd = [
            "makemkvcon",
            "mkv",
            source,
            str(title.id),
            str(output_dir),
            "--progress=-same",
        ]
        _run_makemkv(cmd, on_progress, current_title=title)

    ripped = sorted(output_dir.glob("*.mkv"))
    logger.info("Remux complete: %d file(s)", len(ripped))
    return ripped


def _rip_single_title(
    title: Title,
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None,
) -> Path | None:
    """Rip a single title from disc."""
    source = f"dev:{settings.device}"
    cmd = [
        "makemkvcon",
        "mkv",
        source,
        str(title.id),
        str(output_dir),
        "--progress=-same",
    ]

    _run_makemkv(cmd, on_progress, current_title=title)

    # Find the output file (makemkvcon names files title_XX.mkv)
    candidates = list(
        output_dir.glob(f"*t{title.id:02d}*.mkv")
    ) + list(output_dir.glob(f"*title{title.id:02d}*.mkv"))
    if candidates:
        return candidates[0]

    # Fallback: check for any new .mkv file
    mkvs = sorted(
        output_dir.glob("*.mkv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return mkvs[0] if mkvs else None


class _ProgressParser:
    """Parse makemkvcon output lines into RipProgress updates.

    Encapsulates the state tracking (title, bytes, rate) and the
    six regex handlers that were previously inline in _run_makemkv.
    """

    def __init__(
        self,
        current_title: Title | None = None,
        debug_harness: _ProgressDebugHarness | None = None,
    ) -> None:
        self.title_id = current_title.id if current_title else 0
        self.title_name = (
            current_title.name if current_title else "Unknown"
        )
        self.start_time = time.monotonic()
        self.last_current = 0
        self.last_total = 0
        self.last_percent = 0.0
        self.last_rate: float | None = None
        self.sample_time: float | None = None
        self.sample_bytes: int | None = None
        self._debug = debug_harness

    def parse_line(self, line: str) -> tuple[RipProgress, str] | None:
        """Try each handler; return (progress, source) or None."""
        result = (
            self._try_progress_title(line)
            or self._try_current_title(line)
            or self._try_human_action(line)
            or self._try_human_operation(line)
            or self._try_prgv(line)
            or self._try_human_progress(line)
        )
        if result is None and self._debug:
            if line.startswith("PR") or line.startswith("Current "):
                self._debug.record(
                    "unparsed_progress_line", line=line,
                )
        return result

    def _build_progress(self) -> RipProgress:
        return RipProgress(
            title_id=self.title_id,
            title_name=self.title_name,
            percent=self.last_percent,
            current_bytes=self.last_current,
            total_bytes=self.last_total,
            eta_seconds=_calc_eta(self.last_percent, self.start_time),
            bytes_per_second=self.last_rate,
        )

    def _try_progress_title(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = PROGRESS_TITLE_RE.match(line)
        if not m:
            return None
        self.title_name = m.group(1) or self.title_name
        if self._debug:
            self._debug.record(
                "line_parsed", kind="PRGT",
                title_name=self.title_name,
            )
        return self._build_progress(), "PRGT"

    def _try_current_title(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = CURRENT_TITLE_RE.match(line)
        if not m:
            return None
        self.title_id = int(m.group(1))
        self.title_name = m.group(2)
        if self._debug:
            self._debug.record(
                "line_parsed", kind="PRGC",
                title_id=self.title_id,
                title_name=self.title_name,
            )
        return self._build_progress(), "PRGC"

    def _try_human_action(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = HUMAN_ACTION_RE.match(line)
        if not m:
            return None
        self.title_name = m.group(1).strip() or self.title_name
        if self._debug:
            self._debug.record(
                "line_parsed", kind="HUMAN_ACTION",
                title_name=self.title_name,
            )
        return self._build_progress(), "HUMAN_ACTION"

    def _try_human_operation(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = HUMAN_OPERATION_RE.match(line)
        if not m:
            return None
        self.title_name = m.group(1).strip() or self.title_name
        if self._debug:
            self._debug.record(
                "line_parsed", kind="HUMAN_OPERATION",
                title_name=self.title_name,
            )
        return self._build_progress(), "HUMAN_OPERATION"

    def _try_prgv(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = PROGRESS_RE.match(line)
        if not m:
            return None
        current, maximum = _parse_progress_values(m)
        percent = _clamp_percent(
            (current / maximum * 100) if maximum > 0 else 0
        )
        if self._debug:
            self._debug.record(
                "line_parsed", kind="PRGV",
                current=current, maximum=maximum, percent=percent,
            )
        now = time.monotonic()
        rate: float | None = None
        if (
            self.sample_time is not None
            and self.sample_bytes is not None
        ):
            elapsed = now - self.sample_time
            delta_bytes = current - self.sample_bytes
            if elapsed > 0 and delta_bytes >= 0:
                rate = delta_bytes / elapsed
        self.sample_time = now
        self.sample_bytes = current
        self.last_current = current
        self.last_total = maximum
        self.last_percent = percent
        self.last_rate = rate

        progress = RipProgress(
            title_id=self.title_id,
            title_name=self.title_name,
            percent=percent,
            current_bytes=current,
            total_bytes=maximum,
            eta_seconds=_calc_eta(percent, self.start_time),
            bytes_per_second=rate,
        )
        return progress, "PRGV"

    def _try_human_progress(
        self, line: str,
    ) -> tuple[RipProgress, str] | None:
        m = HUMAN_PROGRESS_RE.match(line)
        if not m:
            return None
        percent = _parse_human_progress_values(m)
        if self._debug:
            self._debug.record(
                "line_parsed", kind="HUMAN_PROGRESS",
                current_percent=int(m.group(1)),
                total_percent=int(m.group(2)),
                percent=percent,
            )
        self.last_current = 0
        self.last_total = 0
        self.last_percent = percent
        self.last_rate = None
        self.sample_time = None
        self.sample_bytes = None
        progress = RipProgress(
            title_id=self.title_id,
            title_name=self.title_name,
            percent=percent,
            current_bytes=0,
            total_bytes=0,
            eta_seconds=_calc_eta(percent, self.start_time),
            bytes_per_second=None,
        )
        return progress, "HUMAN_PROGRESS"


def _run_makemkv(
    cmd: list[str],
    on_progress: ProgressCallback | None,
    current_title: Title | None = None,
) -> None:
    """Execute makemkvcon and parse progress output.

    Uses a pseudo-TTY for stdout so makemkvcon line-buffers its
    progress output instead of block-buffering to a pipe.
    """
    global _active_process

    if not shutil.which("makemkvcon"):
        raise MakeMKVNotFoundError()

    debug_harness = _ProgressDebugHarness.from_environment(
        cmd, current_title
    )

    # Create a PTY so makemkvcon sees a terminal and line-buffers output
    master_fd, slave_fd = os.openpty()

    process = subprocess.Popen(
        cmd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)  # Parent doesn't need the slave end
    if debug_harness:
        debug_harness.record("process_start", pid=process.pid)

    with _process_lock:
        _active_process = process

    parser = _ProgressParser(current_title, debug_harness)

    # Wrap the master fd in a buffered text reader for readline()
    master_file = open(master_fd, closefd=True)  # noqa: SIM115

    return_code: int | None = None
    error_message: str | None = None
    try:
        while True:
            try:
                line = master_file.readline()
            except OSError:
                # PTY returns EIO when the slave side closes
                break
            if not line:
                break
            line = line.strip()
            if debug_harness:
                debug_harness.record("raw_line", line=line)

            result = parser.parse_line(line)
            if result:
                progress, source = result
                _emit_progress_update(
                    progress,
                    source=source,
                    on_progress=on_progress,
                    debug_harness=debug_harness,
                )

        return_code = process.wait()
        if debug_harness:
            debug_harness.record(
                "process_exit",
                return_code=return_code,
            )
        if return_code != 0:
            # Check if we were cancelled
            if return_code < 0:
                raise RipCancelledError("Rip cancelled by user")
            raise RuntimeError(
                f"makemkvcon exited with code {return_code}"
            )
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        master_file.close()
        with _process_lock:
            _active_process = None
        if debug_harness:
            if error_message:
                debug_harness.record(
                    "process_error",
                    error=error_message,
                )
            debug_harness.record("session_end")
            debug_harness.close()


def _calc_eta(percent: float, start_time: float) -> int | None:
    """Estimate remaining seconds based on progress so far."""
    if percent <= 0:
        return None
    elapsed = time.monotonic() - start_time
    if elapsed < 2:
        return None
    total_estimated = elapsed / (percent / 100)
    remaining = total_estimated - elapsed
    return max(0, int(remaining))


def _parse_progress_values(match: re.Match[str]) -> tuple[int, int]:
    """Return (current, maximum) from a PRGV match.

    MakeMKV variants differ in which field carries max/total progress.
    Prefer a positive denominator and fall back to zero when unavailable.
    """
    first = int(match.group(1))
    second = int(match.group(2))
    third = int(match.group(3))

    if third > 0:
        return first, third
    if second > 0:
        return first, second
    return first, 0


def _parse_human_progress_values(match: re.Match[str]) -> float:
    """Return a normalized progress percent from human-readable output."""
    current_percent = int(match.group(1))
    total_percent = int(match.group(2))
    percent = total_percent if total_percent > 0 else current_percent
    return _clamp_percent(float(percent))


def _clamp_percent(percent: float) -> float:
    if percent < 0:
        return 0.0
    if percent > 100:
        return 100.0
    return percent


def summarize_progress_trace(
    trace_path: Path,
    tail_size: int = 15,
) -> dict[str, object]:
    """Summarize a RIPPER progress debug trace."""
    parsed_counts: Counter[str] = Counter()
    emitted_counts: Counter[str] = Counter()
    raw_tail: deque[str] = deque(maxlen=tail_size)
    unparsed_tail: deque[str] = deque(maxlen=tail_size)
    final_progress: dict[str, object] | None = None
    process_exit_code: int | None = None
    total_events = 0
    raw_lines = 0
    malformed_lines = 0

    with trace_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = line.strip()
            if not row:
                continue
            try:
                event = json.loads(row)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue

            total_events += 1
            event_name = str(event.get("event", ""))
            if event_name == "raw_line":
                raw_lines += 1
                raw_tail.append(str(event.get("line", "")))
                continue

            if event_name == "line_parsed":
                parsed_counts[str(event.get("kind", "UNKNOWN"))] += 1
                continue

            if event_name == "progress_emit":
                emitted_counts[str(event.get("source", "UNKNOWN"))] += 1
                progress = event.get("progress")
                if isinstance(progress, dict):
                    final_progress = progress
                continue

            if event_name == "unparsed_progress_line":
                unparsed_tail.append(str(event.get("line", "")))
                continue

            if event_name == "process_exit":
                code = event.get("return_code")
                if isinstance(code, int):
                    process_exit_code = code

    return {
        "total_events": total_events,
        "raw_lines": raw_lines,
        "malformed_lines": malformed_lines,
        "parsed_counts": dict(parsed_counts),
        "emitted_counts": dict(emitted_counts),
        "raw_tail": list(raw_tail),
        "unparsed_progress_lines": list(unparsed_tail),
        "final_progress": final_progress,
        "process_exit_code": process_exit_code,
    }


def _emit_progress_update(
    progress: RipProgress,
    source: str,
    on_progress: ProgressCallback | None,
    debug_harness: _ProgressDebugHarness | None,
) -> None:
    if debug_harness:
        debug_harness.record(
            "progress_emit",
            source=source,
            callback_registered=on_progress is not None,
            progress=_progress_to_dict(progress),
        )
    if on_progress:
        on_progress(progress)


def _progress_to_dict(progress: RipProgress) -> dict[str, object]:
    return {
        "title_id": progress.title_id,
        "title_name": progress.title_name,
        "percent": progress.percent,
        "current_bytes": progress.current_bytes,
        "total_bytes": progress.total_bytes,
        "eta_seconds": progress.eta_seconds,
        "bytes_per_second": progress.bytes_per_second,
    }


def _title_to_dict(title: Title | None) -> dict[str, object] | None:
    if title is None:
        return None
    return {
        "id": title.id,
        "name": title.name,
    }


def _env_flag_enabled(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _resolve_progress_debug_path() -> Path:
    configured_path = os.getenv(_PROGRESS_DEBUG_FILE_ENV, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    pid = os.getpid()
    return Path("/tmp") / f"ripper-progress-{timestamp}-{pid}.jsonl"
