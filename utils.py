import time
import asyncio
import random
import string
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import UserNotParticipant, MessageNotModified, FloodWait
from config import Config

# Cache to store progress data in background without sending API requests
PROGRESS_CACHE = {}


def get_random_mix_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def humanbytes(size):
    if not size:
        return "0B"
    power = 2 ** 10
    n = 0
    dic_power_ten = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti', 5: 'Pi'}
    while size > power and n < len(dic_power_ten) - 1:
        size /= power
        n += 1
    return f"{round(size, 2)} {dic_power_ten[n]}B"


def time_formatter(milliseconds: int) -> str:
    """Human readable duration, e.g. '1d 2h 3m 4s'."""
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)      # FIX: was divmod(hours, 60) with hours undefined
    days, hours = divmod(hours, 24)
    tmp = (
        (f"{days}d " if days else "") +
        (f"{hours}h " if hours else "") +
        (f"{minutes}m " if minutes else "") +
        (f"{seconds}s" if seconds else "")
    )
    return tmp if tmp else "0s"


def time_colon(seconds: float) -> str:
    """Compact MM:SS or HH:MM:SS format, e.g. '00:51' or '01:23:45'."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_slider_bar(percentage: float, width: int = 26) -> str:
    """Slider-style bar: a continuous ━ track with a ● knob at the current position."""
    percentage = max(0, min(100, percentage))
    pos = int(width * percentage / 100)
    pos = max(0, min(width - 1, pos))
    return "━" * pos + "●" + "━" * (width - pos - 1)


async def progress_bar(
    current,
    total,
    status_type,
    message: Message,
    start_time,
    task_id,
    user,
    file_name: str = "File",
    engine: str = "TDLib v1.8.66",
    task_number: int = 1,
    total_tasks: int = 1,
    force_update: bool = False,
):
    now = time.time()

    # Silently cache progress data in memory
    PROGRESS_CACHE[task_id] = {
        "current": current,
        "total": total,
        "status_type": status_type,
        "start_time": start_time,
        "message": message,
        "user": user,
        "file_name": file_name,
        "engine": engine,
        "task_number": task_number,
        "total_tasks": total_tasks,
    }

    # Only edit the message on explicit trigger (Refresh button or phase start/complete)
    if not (force_update or current == total):
        return

    diff = now - start_time
    percentage = (current * 100 / total) if total > 0 else 0
    speed = current / diff if diff > 0 else 0
    elapsed_seconds = diff
    eta_seconds = (total - current) / speed if speed > 0 else 0

    bar = build_slider_bar(percentage, width=26)
    eta_str = time_colon(eta_seconds) if speed > 0 else "-"
    elapsed_str = time_colon(elapsed_seconds)

    text = (
        f"🚀 <b>Task Running: {task_number:02d}/{total_tasks:02d}</b>\n\n"
        f"╭──────────────────────────────────────╮\n"
        f"│ status. {status_type}\n"
        f"│\n"
        f"│ {bar} {round(percentage, 1)}%\n"
        f"│\n"
        f"│ 📂 File       : {file_name}\n"
        f"│ 📦 Processed  : {humanbytes(current)} / {humanbytes(total)}\n"
        f"│ ⚡ Speed      : {humanbytes(speed)}/s\n"
        f"│ ⏳ ETA        : {eta_str}\n"
        f"│ 🕒 Elapsed    : {elapsed_str}\n"
        f"│\n"
        f"│ 📤 Upload     : Telegram\n"
        f"│ ⚙ Engine      : {engine}\n"
        f"╰──────────────────────────────────────╯\n\n"
        f"🛑 Cancel: /stop_{task_id}"
    )

    refresh_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_progress_{task_id}")]]
    )

    try:
        await message.edit_text(text, reply_markup=refresh_btn)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass


async def check_force_sub(client, user_id):
    buttons = []
    for channel in Config.FORCE_SUB_CHANNELS:
        try:
            member = await client.get_chat_member(channel, user_id)
            if member.status in ["kicked", "left"]:
                buttons.append([InlineKeyboardButton(f"Join @{channel}", url=f"https://t.me/{channel}")])
        except UserNotParticipant:
            buttons.append([InlineKeyboardButton(f"Join @{channel}", url=f"https://t.me/{channel}")])
        except Exception:
            pass
    return buttons
