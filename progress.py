"""
Progress bar rendering + throttled progress callback for Pyrogram
download/upload, styled to match the boxed premium progress UI.
"""

import time
import asyncio

import config


def humanbytes(size: float) -> str:
    if not size:
        return "0B"
    power = 1024
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{size:.2f}{units[n]}"


def time_formatter(seconds: float) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "-"
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    result = []
    for name, count in periods:
        value, seconds = divmod(seconds, count)
        if value:
            result.append(f"{value}{name}")
    return " ".join(result) if result else "0s"


def make_bar(percentage: float, length: int = 20) -> str:
    filled = int(length * percentage / 100)
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


def truncate_name(name: str, limit: int = 30) -> str:
    if len(name) <= limit:
        return name
    return name[: limit - 3] + "..."


def render_progress_box(
    filename: str,
    stage_label: str,
    current: int,
    total: int,
    speed: float,
    elapsed: float,
    stop_token: str,
    engine: str = "Pyrogram v2",
    uploader: str = "Telegram",
) -> str:
    """
    Renders a progress box matching the boxed premium UI style:

    ╔══════════════════════════════════╗
    ║ 📁 filename.mkv                 ║
    ║ [███████░░░░░░░░░░░░] 35%       ║
    ║ ⬇️ 2.4 MB/s                     ║
    ║ 📦 12MB/34MB                    ║
    ║ ⏳ ETA: 8s                       ║
    ╚══════════════════════════════════╝
    """
    total = total or 1
    percentage = min(100, (current * 100 / total))
    bar = make_bar(percentage)
    eta = (total - current) / speed if speed > 0 else 0

    direction_icon = "⬇️" if stage_label.lower().startswith("download") else "⬆️"

    text = (
        f"**Task Running**\n\n"
        f"**{stage_label}:**\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"**Processed:** {humanbytes(current)}\n"
        f"**Size:** {humanbytes(total)}\n"
        f"**Speed:** {humanbytes(speed)}/s\n"
        f"**ETA:** {time_formatter(eta)}\n"
        f"**Elapsed:** {time_formatter(elapsed)}\n"
        f"**Upload:** {uploader}\n"
        f"**Engine:** {engine}\n"
        f"`/stop_{stop_token}`"
    )
    return text


class ProgressTracker:
    """
    Wraps Pyrogram's progress callback signature (current, total) and
    throttles message edits to once every PROGRESS_UPDATE_INTERVAL seconds.
    """

    def __init__(self, message, filename: str, stage_label: str, stop_token: str, cancel_event: asyncio.Event = None):
        self.message = message
        self.filename = filename
        self.stage_label = stage_label
        self.stop_token = stop_token
        self.cancel_event = cancel_event
        self.start_time = time.time()
        self.last_update = 0
        self.last_current = 0
        self.last_time = self.start_time

    async def update(self, current: int, total: int):
        if self.cancel_event and self.cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled by user")

        now = time.time()
        if now - self.last_update < config.PROGRESS_UPDATE_INTERVAL and current != total:
            return
        self.last_update = now

        elapsed = now - self.start_time
        interval = now - self.last_time or 1
        speed = (current - self.last_current) / interval
        self.last_current = current
        self.last_time = now

        text = render_progress_box(
            filename=truncate_name(self.filename),
            stage_label=self.stage_label,
            current=current,
            total=total,
            speed=speed,
            elapsed=elapsed,
            stop_token=self.stop_token,
        )
        try:
            await self.message.edit_text(text)
        except Exception:
            # Ignore "message not modified" / flood errors during progress spam
            pass
