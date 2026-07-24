import os
import sys
import time
import math
import random
import string
import asyncio
import psutil
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
)
from pyrogram.errors import FloodWait, UserNotParticipant

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# System Initialization
os.makedirs("downloads", exist_ok=True)
bot_start_time = time.time()

# MongoDB Setup
mongo_client = AsyncIOMotorClient(config.MONGO_URL)
db = mongo_client["rename_bot_db"]
users_col = db["users"]

# Concurrent Workers Setup
executor = ThreadPoolExecutor(max_workers=500)
semaphore = asyncio.Semaphore(10)
queue = asyncio.Queue(maxsize=100)

CANCEL_TASKS = {}

# Pyrogram Client Setup
app = Client(
    "rename_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=500
)

# ------------------- KOYEB HEALTH CHECK SERVER ------------------- #

async def health_check_handler(request):
    return web.Response(text="OK", status=200)

async def start_health_check_server():
    server = web.Application()
    server.router.add_get('/', health_check_handler)
    server.router.add_get('/health', health_check_handler)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Health check web server running on port {port}")

# ------------------- UTILITY FUNCTIONS ------------------- #

def humanbytes(size):
    if not size:
        return "0B"
    power = 2 ** 10
    n = 0
    dic_power_ten = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
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
           (f"{seconds}s" if seconds else "0s"))
    return tmp

def get_random_mix_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

async def check_force_sub(client, user_id):
    not_joined = []
    for ch in config.FORCE_SUB_CHANNELS:
        try:
            member = await client.get_chat_member(f"@{ch}", user_id)
            if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)

    if not_joined:
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"📢 Join @{ch}", url=f"https://t.me/{ch}")])
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify_sub")])
        return False, InlineKeyboardMarkup(buttons)
    return True, None

async def is_banned(user_id):
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("banned", False):
        return True
    return False

async def get_or_create_user(user):
    existing = await users_col.find_one({"user_id": user.id})
    if not existing:
        doc = {
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name or "",
            "username": user.username or "",
            "thumbnail": None,
            "metadata_title": None,
            "metadata_artist": None,
            "metadata_album": None,
            "metadata_year": None,
            "caption": None,
            "prefix": None,
            "suffix": None,
            "banned": False,
            "joined_date": datetime.utcnow(),
            "last_active": datetime.utcnow(),
            "total_processed": 0
        }
        await users_col.insert_one(doc)
        return doc
    else:
        await users_col.update_one({"user_id": user.id}, {"$set": {"last_active": datetime.utcnow()}})
        return existing

# ------------------- PROGRESS BAR DISPLAY ------------------- #

async def progress_for_pyrogram(current, total, ud_type, message, start, task_id):
    now = time.time()
    diff = now - start
    if task_id in CANCEL_TASKS and CANCEL_TASKS[task_id]:
        raise Exception("CancelledByUser")

    if round(diff % 2.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff)
        time_to_completion = round((total - current) / speed) if speed > 0 else 0

        filled_len = int(12 * current // total)
        bar = '█' * filled_len + '░' * (12 - filled_len)

        tmp = (
            f"<b>Task Running: 1/20 </b>\n\n"
            f"<b>1.{ud_type}:</b>\n"
            f"[{bar}] {round(percentage, 1)}%\n"
            f"<b>Processed:</b> {humanbytes(current)}\n"
            f"<b>Size:</b> {humanbytes(total)}\n"
            f"<b>Speed:</b> {humanbytes(speed)}/s\n"
            f"<b>ETA:</b> {time_formatter(time_to_completion * 1000) if time_to_completion else '-'}\n"
            f"<b>Elapsed:</b> {elapsed_time}s\n"
            f"<b>Upload:</b> Telegram\n"
            f"<b>Engine:</b> Pyrogram Engine\n"
            f"<b>User:</b> {message.chat.first_name} ({message.chat.id})\n"
            f"<code>/stop_{task_id}</code>"
        )
        try:
            await message.edit_text(
                text=tmp,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_progress")]])
            )
        except Exception:
            pass

# ------------------- COMMAND HANDLERS ------------------- #

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message: Message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        return await message.reply("⚠️ <b>You are banned from using this bot!</b>")

    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    await get_or_create_user(message.from_user)

    try:
        stk = await message.reply_sticker(config.START_STICKER)
        await asyncio.sleep(2)
        await stk.delete()
    except Exception:
        pass

    img_url = f"{random.choice(config.PICS_URL)}?r={get_random_mix_id()}"
    start_txt = (
        f"<b>ʜᴇʏ {message.from_user.mention}!</b>\n\n"
        f"<b>ɪ'ᴍ ᴀ ᴘᴏᴡᴇʀғᴜʟ ғɪʟᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ</b> 📝\n\n"
        f"<b>⚡ ғᴇᴀᴛᴜʀᴇs:</b>\n"
        f"➤ ғɪʟᴇ ʀᴇɴᴀᴍɪɴɢ\n"
        f"➤ ᴍᴇᴛᴀᴅᴀᴛᴀ ᴇᴅɪᴛᴏʀ\n"
        f"➤ sᴛʀᴇᴀᴍ ᴇxᴛʀᴀᴄᴛᴏʀ\n"
        f"➤ ғɪʟᴇ ᴄᴏɴᴠᴇʀᴛᴇʀ\n"
        f"➤ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴛʜᴜᴍʙɴᴀɪʟ\n"
        f"➤ ᴀɴᴅ ᴍᴀɴʏ ᴍᴏʀᴇ...\n\n"
        f"sᴇɴᴅ ᴀ ғɪʟᴇ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ! 🚀"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Rename", callback_data="btn_rename"), InlineKeyboardButton("📦 Batch Rename", callback_data="btn_batch")],
        [InlineKeyboardButton("🖼️ Thumbnail", callback_data="btn_thumb"), InlineKeyboardButton("📋 Metadata", callback_data="btn_meta")],
        [InlineKeyboardButton("✍️ Caption", callback_data="btn_caption"), InlineKeyboardButton("🏷️ Prefix/Suffix", callback_data="btn_prefix")],
        [InlineKeyboardButton("🎬 Media Tools", callback_data="btn_media"), InlineKeyboardButton("🔄 Convert", callback_data="btn_convert")],
        [InlineKeyboardButton("📸 Screenshot", callback_data="btn_ss"), InlineKeyboardButton("🎵 Audio Tools", callback_data="btn_audio")],
        [InlineKeyboardButton("📋 Subtitle", callback_data="btn_sub"), InlineKeyboardButton("🗜️ Compress", callback_data="btn_compress")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"), InlineKeyboardButton("ℹ️ About", callback_data="cmd_about")],
        [InlineKeyboardButton("📊 Stats", callback_data="cmd_stats"), InlineKeyboardButton("🔧 Help", callback_data="cmd_help")]
    ])

    try:
        await message.reply_photo(photo=img_url, caption=start_txt, reply_markup=buttons)
    except Exception:
        await message.reply_photo(photo=config.FALLBACK_PIC, caption=start_txt, reply_markup=buttons)

# ------------------- MAIN ENTRYPOINT ------------------- #

async def main():
    # Start Koyeb Health Check web server
    await start_health_check_server()
    # Start Pyrogram Client
    await app.start()
    logger.info("Bot is successfully running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
