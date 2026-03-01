"""Disc scanning using python-makemkv."""

import hashlib
import logging
import shutil
import struct
import subprocess
from pathlib import Path

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
    """Parse a byte count from makemkvcon output.

    Handles both raw numeric strings ("34474836992") and
    formatted sizes ("32.1 GB", "500 MB").
    """
    s = size_str.strip()
    upper = s.upper()
    multipliers = {"GB": 1_073_741_824, "MB": 1_048_576, "KB": 1024}
    for suffix, mult in multipliers.items():
        if upper.endswith(suffix):
            num_part = s[: -len(suffix)].strip()
            try:
                return int(float(num_part) * mult)
            except ValueError:
                return 0
    cleaned = "".join(c for c in s if c.isdigit())
    return int(cleaned) if cleaned else 0


def scan_disc(
    settings: Settings, backup_dir: Path | None = None,
) -> DiscInfo:
    """Scan the disc (or a backup directory) and return structured info.

    Args:
        settings: App settings.
        backup_dir: If provided, scan from this BDMV backup directory
            instead of the physical disc drive.

    Raises:
        MakeMKVNotFoundError: If makemkvcon is not installed.
        RuntimeError: If scan fails or no titles found.
    """
    if not shutil.which("makemkvcon"):
        raise MakeMKVNotFoundError()

    if backup_dir is not None:
        source = f"file:{backup_dir}"
        logger.info("Scanning backup at %s...", backup_dir)
    else:
        source = f"dev:{settings.device}"
        logger.info("Scanning disc at %s...", settings.device)

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


def _compute_content_hash(hsh_sizes: list[int]) -> str:
    """Compute TheDiscDB ContentHash from M2TS file sizes.

    MD5 over each size as little-endian int64.
    Returns 32-char uppercase hex string, or empty string if no sizes.
    """
    if not hsh_sizes:
        return ""
    md5 = hashlib.md5()
    for size in hsh_sizes:
        md5.update(struct.pack("<q", size))
    return md5.hexdigest().upper()


def compute_hash_from_backup(backup_dir: Path) -> str | None:
    """Compute TheDiscDB ContentHash from a backup's M2TS files.

    Reads file sizes from BDMV/STREAM/*.m2ts, sorted alphabetically,
    and computes MD5 of sizes as little-endian int64.
    """
    stream_dir = backup_dir / "BDMV" / "STREAM"
    if not stream_dir.is_dir():
        logger.warning("No BDMV/STREAM directory in %s", backup_dir)
        return None

    m2ts_files = sorted(stream_dir.glob("*.m2ts"))
    if not m2ts_files:
        logger.warning("No M2TS files in %s", stream_dir)
        return None

    sizes = [f.stat().st_size for f in m2ts_files]
    content_hash = _compute_content_hash(sizes)

    logger.info(
        "Content hash from %d M2TS files: %s",
        len(sizes),
        content_hash,
    )
    return content_hash or None


def _parse_makemkv_output(raw: str, settings: Settings) -> DiscInfo:
    """Parse raw makemkvcon output into DiscInfo."""
    disc_name = "UNKNOWN_DISC"
    title_data: dict[int, dict[str, str]] = {}
    hsh_sizes: list[int] = []

    for line in raw.splitlines():
        # Disc name: CINFO:2,0,"name"
        if line.startswith('CINFO:2,0,"'):
            disc_name = line.split('"')[1]
            continue

        # HSH line: HSH:{index},{filename},{datetime},{size}
        if line.startswith("HSH:"):
            parts = line[4:].split(",")
            if len(parts) >= 4:
                try:
                    hsh_sizes.append(int(parts[3]))
                except ValueError:
                    pass
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
                # Text size (e.g. "32.1 GB") — only use as fallback
                if "size" not in title_data[tid]:
                    title_data[tid]["size"] = value
            case 11:
                # Raw byte count — always prefer over text size
                title_data[tid]["size"] = value
            case 16:
                title_data[tid]["source_file"] = value
            case 26:
                title_data[tid]["segments_map"] = value

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
            source_file=data.get("source_file", ""),
            is_main_feature=duration >= settings.min_main_length,
        )
        titles.append(title)

    if not titles:
        raise RuntimeError("No rippable titles found on disc")

    content_hash = _compute_content_hash(hsh_sizes)

    logger.info(
        "Found %d title(s) on disc '%s'", len(titles), disc_name
    )
    return DiscInfo(
        name=disc_name,
        device=settings.device,
        titles=titles,
        content_hash=content_hash or None,
    )
