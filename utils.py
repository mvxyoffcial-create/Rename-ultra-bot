import time
import psutil
import random
import string
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import UserNotParticipant, MessageNotModified
from config import Config

def get_random_mix_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def humanbytes(size):
    if not size:
        return "0B"
    power = 2**10
    n = 0
    dic_power_ten = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {dic_power_ten[n]}B"

def time_formatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((f"{days}d " if days else "") +
           (f"{hours}h " if hours else "") +
           (f"{minutes}m " if minutes else "") +
           (f"{seconds}s" if seconds else ""))
    return tmp if tmp else "0s"

async def progress_bar(current, total, status_type, message: Message, start_time, task_id, user):
    now = time.time()
    diff = now - start_time
    
    # Update UI every 3 seconds to avoid Telegram flood limits
    if round(diff % 3) == 0 or current == total:
        percentage = (current * 100 / total) if total > 0 else 0
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0
        
        # Build 10-block bar matching screenshot: [■■■■■□□□□□]
        filled_length = int(10 * current // total) if total > 0 else 0
        bar = '■' * filled_length + '□' * (10 - filled_length)
        
        eta_str = time_formatter(time_to_completion) if time_to_completion > 0 else "-"
        elapsed_str = time_formatter(elapsed_time)
        user_name = f"{user.first_name}" if user.first_name else f"User"
        
        # Display heading dynamically based on current phase
        header = f"1.{status_type}:"
        
        text = (
            f"<b>{header}</b>\n"
            f"[{bar}] {round(percentage)}%\n"
            f"<b>Processed:</b> {humanbytes(current)}\n"
            f"<b>Size:</b> {humanbytes(total)}\n"
            f"<b>Speed:</b> {humanbytes(speed)}/s\n"
            f"<b>ETA:</b> {eta_str}\n"
            f"<b>Elapsed:</b> {elapsed_str}\n"
            f"<b>Upload:</b> Telegram\n"
            f"<b>Engine:</b> TDLib v1.8.66\n"
            f"{user_name} ({user.id})\n"
            f"/stop_{task_id}"
        )
        
        refresh_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_progress_{task_id}")]
        ])
        
        try:
            await message.edit_text(text, reply_markup=refresh_btn)
        except MessageNotModified:
            pass
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
