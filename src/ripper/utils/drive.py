"""Drive detection and control utilities."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def check_drive(device: str) -> bool:
    """Check if a disc is present in the drive.

    Returns True if media is detected, False otherwise.
    """
    # Check device exists
    try:
        result = subprocess.run(
            ["blkid", device],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback to udevadm
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", device],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "ID_CDROM_MEDIA=1" in result.stdout:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False


def eject_disc(device: str) -> bool:
    """Eject the disc from the drive.

    Returns True if eject succeeded.
    """
    try:
        result = subprocess.run(
            ["eject", device],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("Disc ejected from %s", device)
            return True
        logger.warning("Eject failed for %s: %s", device, result.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Eject failed for %s: %s", device, e)
    return False


def wait_for_disc(device: str, timeout_seconds: int = 60) -> bool:
    """Wait for a disc to be inserted and ready.

    Polls every 2 seconds until a disc is detected or timeout.

    Returns True if disc detected, False on timeout.
    """
    import time

    elapsed = 0
    while elapsed < timeout_seconds:
        if check_drive(device):
            return True
        time.sleep(2)
        elapsed += 2

    return False
