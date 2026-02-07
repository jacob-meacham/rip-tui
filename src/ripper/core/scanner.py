"""Disc scanning using python-makemkv."""

import logging
import shutil
import subprocess

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, Title

logger = logging.getLogger(__name__)


class MakeMKVNotFoundError(RuntimeError):
    """Raised when makemkvcon is not installed."""

    def __init__(self) -> None:
        super().__init__(
            "makemkvcon not found. "
            "Install MakeMKV: https://www.makemkv.com/download/"
        )


def _parse_duration(duration_str: str) -> int:
    """Parse 'H:MM:SS' into total seconds."""
    parts = duration_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def _parse_raw_byte_count(size_str: str) -> int:
    """Parse a raw byte count from makemkvcon output.

    Expects a numeric string that may contain whitespace or separators
    (e.g. "34474836992"). Does NOT handle formatted sizes like "1.5 GB".
    """
    cleaned = "".join(c for c in size_str if c.isdigit())
    return int(cleaned) if cleaned else 0


def scan_disc(settings: Settings) -> DiscInfo:
    """Scan the disc and return structured info.

    Raises:
        MakeMKVNotFoundError: If makemkvcon is not installed.
        RuntimeError: If scan fails or no titles found.
    """
    if not shutil.which("makemkvcon"):
        raise MakeMKVNotFoundError()

    logger.info("Scanning disc at %s...", settings.device)

    source = f"dev:{settings.device}"

    try:
        result = subprocess.run(
            ["makemkvcon", "-r", "info", source],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Disc scan timed out after 5 minutes")

    if result.returncode != 0 and not result.stdout:
        raise RuntimeError(
            f"makemkvcon failed: {result.stderr.strip()}"
        )

    return _parse_makemkv_output(result.stdout, settings)


def _parse_makemkv_output(raw: str, settings: Settings) -> DiscInfo:
    """Parse raw makemkvcon output into DiscInfo."""
    disc_name = "UNKNOWN_DISC"
    title_data: dict[int, dict[str, str]] = {}

    for line in raw.splitlines():
        # Disc name: CINFO:2,0,"name"
        if line.startswith('CINFO:2,0,"'):
            disc_name = line.split('"')[1]
            continue

        # Title info: TINFO:title_id,code,subcode,"value"
        if not line.startswith("TINFO:"):
            continue

        # Parse TINFO:N,code,subcode,"value"
        try:
            prefix, value_part = line.split(",", 1)
            tid = int(prefix.split(":")[1])
            parts = value_part.split(",", 2)
            code = int(parts[0])
            value = parts[2].strip('"')
        except (ValueError, IndexError):
            continue

        if tid not in title_data:
            title_data[tid] = {}

        match code:
            case 2:
                title_data[tid]["name"] = value
            case 8:
                title_data[tid]["chapters"] = value
            case 9:
                title_data[tid]["duration"] = value
            case 10:
                title_data[tid]["size"] = value
            case 11:
                # Prefer numeric size if text size already set
                if "size" not in title_data[tid]:
                    title_data[tid]["size"] = value

    # Build Title objects
    titles: list[Title] = []
    for tid in sorted(title_data.keys()):
        data = title_data[tid]
        duration = _parse_duration(data.get("duration", "0:00:00"))

        if duration < settings.min_extra_length:
            continue

        title = Title(
            id=tid,
            name=data.get("name", f"Title {tid}"),
            duration_seconds=duration,
            size_bytes=_parse_raw_byte_count(data.get("size", "0")),
            chapter_count=int(data.get("chapters", "0")),
            is_main_feature=duration >= settings.min_main_length,
        )
        titles.append(title)

    if not titles:
        raise RuntimeError("No rippable titles found on disc")

    logger.info(
        "Found %d title(s) on disc '%s'", len(titles), disc_name
    )
    return DiscInfo(name=disc_name, device=settings.device, titles=titles)
