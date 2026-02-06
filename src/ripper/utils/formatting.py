"""Duration and size formatting utilities."""


def fmt_duration(seconds: int) -> str:
    """Format seconds as 'Xh XXm XXs'."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m:02d}m {s:02d}s"


def fmt_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.1f} GB"
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.0f} MB"
    return f"{size_bytes} bytes"


def fmt_rate(bytes_per_second: float) -> str:
    """Format transfer rate as human-readable string."""
    if bytes_per_second >= 1_073_741_824:
        return f"{bytes_per_second / 1_073_741_824:.1f} GB/s"
    if bytes_per_second >= 1_048_576:
        return f"{bytes_per_second / 1_048_576:.1f} MB/s"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.0f} KB/s"
    return f"{bytes_per_second:.0f} B/s"
