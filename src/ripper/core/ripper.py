"""Ripping engine with progress tracking and cancellation."""

import logging
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ripper.config.settings import Settings
from ripper.core.disc import Title
from ripper.core.scanner import MakeMKVNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class RipProgress:
    """Progress update from a rip operation."""

    title_id: int
    title_name: str
    percent: float  # 0.0 - 100.0
    current_bytes: int
    total_bytes: int
    eta_seconds: int | None = None


ProgressCallback = Callable[[RipProgress], None]

# makemkvcon progress line pattern: PRGV:current,total,max
PROGRESS_RE = re.compile(r"^PRGV:(\d+),(\d+),(\d+)")
# Current title being processed: PRGC:id,code,"name"
CURRENT_TITLE_RE = re.compile(r'^PRGC:(\d+),\d+,"(.*)"')


class RipCancelledError(Exception):
    """Raised when a rip is cancelled by the user."""


# Global reference to the active makemkvcon process for cancellation
_active_process: subprocess.Popen | None = None
_process_lock = threading.Lock()


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

    cmd = [
        "makemkvcon",
        "mkv",
        "disc:0",
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


def _rip_single_title(
    title: Title,
    output_dir: Path,
    settings: Settings,
    on_progress: ProgressCallback | None,
) -> Path | None:
    """Rip a single title from disc."""
    cmd = [
        "makemkvcon",
        "mkv",
        "disc:0",
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


def _run_makemkv(
    cmd: list[str],
    on_progress: ProgressCallback | None,
    current_title: Title | None = None,
) -> None:
    """Execute makemkvcon and parse progress output."""
    global _active_process

    if not shutil.which("makemkvcon"):
        raise MakeMKVNotFoundError()

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with _process_lock:
        _active_process = process

    assert process.stdout is not None

    title_id = current_title.id if current_title else 0
    title_name = current_title.name if current_title else "Unknown"
    start_time = time.monotonic()

    try:
        for line in process.stdout:
            line = line.strip()

            # Update current title from PRGC lines
            title_match = CURRENT_TITLE_RE.match(line)
            if title_match:
                title_id = int(title_match.group(1))
                title_name = title_match.group(2)

            # Parse progress
            progress_match = PROGRESS_RE.match(line)
            if progress_match and on_progress:
                current = int(progress_match.group(1))
                maximum = int(progress_match.group(3))
                percent = (
                    (current / maximum * 100) if maximum > 0 else 0
                )

                eta = _calc_eta(percent, start_time)
                on_progress(
                    RipProgress(
                        title_id=title_id,
                        title_name=title_name,
                        percent=percent,
                        current_bytes=current,
                        total_bytes=maximum,
                        eta_seconds=eta,
                    )
                )

        return_code = process.wait()
        if return_code != 0:
            # Check if we were cancelled
            if return_code < 0:
                raise RipCancelledError("Rip cancelled by user")
            raise RuntimeError(
                f"makemkvcon exited with code {return_code}"
            )
    finally:
        with _process_lock:
            _active_process = None


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
