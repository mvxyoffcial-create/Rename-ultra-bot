import time
import math
import psutil
import random
import string
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import UserNotParticipant
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
    tmp = ((f"{days}d, " if days else "") +
           (f"{hours}h, " if hours else "") +
           (f"{minutes}m, " if minutes else "") +
           (f"{seconds}s, " if seconds else ""))
    return tmp[:-2] if tmp else "0s"

async def progress_bar(current, total, status_text, message: Message, start_time, task_id, user):
    now = time.time()
    diff = now - start_time
    if round(diff % 5) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0
        
        filled_length = int(16 * current // total)
        bar = '█' * filled_length + '░' * (16 - filled_length)
        
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        text = (
            f"<b>Task:</b> {task_id}\n"
            f"<b>Status:</b> {status_text}\n"
            f"<b>Progress:</b> {round(percentage, 1)}%\n\n"
            f"<b>📥 Bar:</b> [{bar}] {round(percentage, 1)}%\n"
            f"<b>📊 Processed:</b> {humanbytes(current)} / {humanbytes(total)}\n"
            f"<b>⚡ Speed:</b> {humanbytes(speed)}/s\n"
            f"<b>⏱️ ETA:</b> {time_formatter(time_to_completion)} remaining\n"
            f"<b>⏰ Elapsed:</b> {time_formatter(elapsed_time)}\n"
            f"<b>📤 Upload:</b> Telegram\n"
            f"<b>🔧 Engine:</b> TDLib / Pyrogram\n"
            f"<b>👤 User:</b> {user.mention}\n\n"
            f"<b>📊 System Status:</b>\n"
            f"🖥️ <b>CPU:</b> {cpu}% | 💾 <b>RAM:</b> {humanbytes(ram.used)}/{humanbytes(ram.total)}\n"
            f"💿 <b>Disk:</b> {humanbytes(disk.used)}/{humanbytes(disk.total)}"
        )
        try:
            await message.edit_text(text)
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
