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

    # 1. Sticker Sequence
    try:
        stk = await message.reply_sticker(config.START_STICKER)
        await asyncio.sleep(2)
        await stk.delete()
    except Exception:
        pass

    # 2. Image Banner
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

@app.on_message(filters.command("help") & filters.private)
async def help_handler(client, message: Message):
    if await is_banned(message.from_user.id): return
    is_joined, markup = await check_force_sub(client, message.from_user.id)
    if not is_joined: return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    help_txt = (
        "<b>✨ ʜᴏᴡ ᴛᴏ ᴜsᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ ✨</b>\n\n"
        "<b>📝 ғɪʟᴇ ʀᴇɴᴀᴍɪɴɢ:</b>\n• sᴇɴᴅ ᴀɴʏ ғɪʟᴇ/ᴠɪᴅᴇᴏ/ᴀᴜᴅɪᴏ\n• ʀᴇᴘʟʏ ᴛᴏ ɪᴛ ᴡɪᴛʜ ɴᴇᴡ ɴᴀᴍᴇ + ᴇxᴛᴇɴsɪᴏɴ\n• ʙᴏᴛ ᴡɪʟʟ ʀᴇɴᴀᴍᴇ & sᴇɴᴅ ʙᴀᴄᴋ\n\n"
        "<b>🖼️ ᴛʜᴜᴍʙɴᴀɪʟ:</b>\n/thumbnail - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴛʜᴜᴍʙɴᴀɪʟ\n/delthumbnail - ʀᴇᴍᴏᴠᴇ ᴛʜᴜᴍʙɴᴀɪʟ\n\n"
        "<b>📋 ᴍᴇᴛᴀᴅᴀᴛᴀ:</b>\n/metadata - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴍᴇᴛᴀᴅᴀᴛᴀ\n/delmetadata - ʀᴇᴍᴏᴠᴇ ᴍᴇᴛᴀᴅᴀᴛᴀ\n\n"
        "<b>✍️ ᴄᴀᴘᴛɪᴏɴ:</b>\n/caption - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴄᴀᴘᴛɪᴏɴ\n/delcaption - ʀᴇᴍᴏᴠᴇ ᴄᴀᴘᴛɪᴏɴ\n\n"
        "<b>🏷️ ᴘʀᴇғɪx/sᴜғғɪx:</b>\n/prefix - ᴀᴅᴅ ᴘʀᴇғɪx ᴛᴏ ғɪʟᴇɴᴀᴍᴇ\n/suffix - ᴀᴅᴅ sᴜғғɪx ᴛᴏ ғɪʟᴇɴᴀᴍᴇ\n\n"
        "<b>ℹ️ ᴏᴛʜᴇʀ ᴄᴏᴍᴍᴀɴᴅs:</b>\n/info - ʏᴏᴜʀ ᴅᴇᴛᴀɪʟs ᴡɪᴛʜ ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ\n/settings - ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs\n/ping - ᴄʜᴇᴄᴋ ʙᴏᴛ ʀᴇsᴘᴏɴsᴇ ᴛɪᴍᴇ\n/about - ʙᴏᴛ ɪɴғᴏʀᴍᴀᴛɪᴏɴ"
    )
    await message.reply(help_txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Home", callback_data="home")]]))

@app.on_message(filters.command("about") & filters.private)
async def about_handler(client, message: Message):
    if await is_banned(message.from_user.id): return
    is_joined, markup = await check_force_sub(client, message.from_user.id)
    if not is_joined: return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    bot_info = await client.get_me()
    about_txt = (
        f"<b>╭────[ ᴍʏ ᴅᴇᴛᴀɪʟs ]────⍟\n\n"
        f"├⍟ Mʏ Nᴀᴍᴇ : {bot_info.first_name}\n"
        f"├⍟ Dᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👨‍💻\n"
        f"├⍟ Oᴡɴᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👑\n"
        f"├⍟ Lɪʙʀᴀʀʏ : <a href='https://github.com/pyrogram/pyrogram'>ᴘʏʀᴏɢʀᴀᴍ ᴠ2</a> 📚\n"
        f"├⍟ Lᴀɴɢᴜᴀɢᴇ : <a href='https://www.python.org/'>ᴘʏᴛʜᴏɴ 3</a> 🐍\n"
        f"├⍟ Dᴀᴛᴀʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a> 🍃\n"
        f"├⍟ Sᴇʀᴠᴇʀ : ᴅᴇᴅɪᴄᴀᴛᴇᴅ ᴠᴘs ☁️\n"
        f"├⍟ Fᴇᴀᴛᴜʀᴇ : ғɪʟᴇ ʀᴇɴᴀᴍᴇʀ 📝\n"
        f"├⍟ Wᴏʀᴋᴇʀs : 500 ᴄᴏɴᴄᴜʀʀᴇɴᴛ ⚡\n"
        f"├⍟ Bᴜɪʟᴅ Sᴛᴀᴛᴜs : ᴠ2.0 [ ᴜʟᴛʀᴀ ] 🚀\n"
        f"╰───────────────⍟</b>"
    )
    await message.reply(about_txt, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Home", callback_data="home")]]))

@app.on_message(filters.command("info") & filters.private)
async def info_handler(client, message: Message):
    if await is_banned(message.from_user.id): return
    is_joined, markup = await check_force_sub(client, message.from_user.id)
    if not is_joined: return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    user = message.from_user
    db_user = await get_or_create_user(user)

    info_text = (
        f"<b>📋 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>\n\n"
        f"➲ <b>First Name:</b> {user.first_name}\n"
        f"➲ <b>Last Name:</b> {user.last_name or 'N/A'}\n"
        f"➲ <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"➲ <b>Data Centre:</b> {user.dc_id or 'Unknown'}\n"
        f"➲ <b>User Name:</b> @{user.username if user.username else 'N/A'}\n"
        f"➲ <b>User 𝖫𝗂𝗇𝗄:</b> <a href='tg://user?id={user.id}'>Click Here</a>\n\n"
        f"<b>📊 Bot Usage:</b>\n"
        f"➲ <b>Files Processed:</b> {db_user.get('total_processed', 0)}\n"
        f"➲ <b>Joined:</b> {db_user.get('joined_date').strftime('%Y-%m-%d') if db_user.get('joined_date') else 'N/A'}"
    )

    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info"), InlineKeyboardButton("🏠 Home", callback_data="home")]])
    
    photos = [p async for p in client.get_chat_photos(user.id, limit=1)]
    if photos:
        await message.reply_photo(photo=photos[0].file_id, caption=info_text, reply_markup=buttons)
    else:
        await message.reply(info_text, reply_markup=buttons)

@app.on_message(filters.command("settings") & filters.private)
async def settings_handler(client, message: Message):
    if await is_banned(message.from_user.id): return
    is_joined, markup = await check_force_sub(client, message.from_user.id)
    if not is_joined: return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    user_data = await get_or_create_user(message.from_user)
    
    thumb_status = "✅ Set" if user_data.get("thumbnail") else "❌ Not Set"
    meta_status = "✅ Set" if user_data.get("metadata_title") else "❌ Not Set"
    caption_status = "✅ Set" if user_data.get("caption") else "❌ Not Set"
    prefix = user_data.get("prefix") or "None"
    suffix = user_data.get("suffix") or "None"

    settings_text = (
        f"<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>\n\n"
        f"🖼️ <b>Thumbnail:</b> {thumb_status}\n"
        f"📋 <b>Metadata:</b> {meta_status}\n"
        f"✍️ <b>Caption:</b> {caption_status}\n"
        f"🏷️ <b>Prefix:</b> {prefix}\n"
        f"🏷️ <b>Suffix:</b> {suffix}\n\n"
        f"<b>📊 Stats:</b>\n"
        f"📁 <b>Files Processed:</b> {user_data.get('total_processed', 0)}"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="cmd_thumb"), InlineKeyboardButton("🗑️ Del Thumbnail", callback_data="del_thumb")],
        [InlineKeyboardButton("📋 Set Metadata", callback_data="cmd_meta"), InlineKeyboardButton("🗑️ Del Metadata", callback_data="del_meta")],
        [InlineKeyboardButton("✍️ Set Caption", callback_data="cmd_caption"), InlineKeyboardButton("🗑️ Del Caption", callback_data="del_caption")],
        [InlineKeyboardButton("🏷️ Prefix", callback_data="cmd_prefix"), InlineKeyboardButton("🏷️ Suffix", callback_data="cmd_suffix")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ])

    await message.reply(settings_text, reply_markup=buttons)

@app.on_message(filters.command("ping") & filters.private)
async def ping_handler(client, message: Message):
    if await is_banned(message.from_user.id): return
    is_joined, markup = await check_force_sub(client, message.from_user.id)
    if not is_joined: return await message.reply("⚠️ <b>You must join our channels to use this bot!</b>", reply_markup=markup)

    start = time.time()
    msg = await message.reply("🏓 <b>Pinging...</b>")
    end = time.time()
    ping_time = round((end - start) * 1000, 2)
    uptime = time_formatter((time.time() - bot_start_time) * 1000)

    res_text = (
        f"<b>🏓 ᴘᴏɴɢ!</b>\n\n"
        f"<b>⏱️ Response Time:</b> <code>{ping_time}ms</code>\n"
        f"<b>📡 Bot Uptime:</b> <code>{uptime}</code>\n"
        f"<b>⚡ Workers:</b> <code>500</code>\n"
        f"<b>🗄️ Database:</b> <code>Connected ✅</code>"
    )
    await msg.edit_text(res_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="cmd_ping"), InlineKeyboardButton("🏠 Home", callback_data="home")]]))

# ------------------- ADMIN COMMANDS ------------------- #

@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client, message: Message):
    if message.from_user.id not in config.ADMIN_IDS: return
    
    total_users = await users_col.count_documents({})
    banned_users = await users_col.count_documents({"banned": True})
    
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = time_formatter((time.time() - bot_start_time) * 1000)

    stats_text = (
        f"<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
        f"<b>👥 Users:</b>\n"
        f"• Total: {total_users}\n"
        f"• Banned: {banned_users}\n\n"
        f"<b>💻 System:</b>\n"
        f"• CPU: {cpu}%\n"
        f"• RAM: {humanbytes(mem.used)}/{humanbytes(mem.total)} ({mem.percent}%)\n"
        f"• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)\n\n"
        f"<b>⚡ Bot:</b>\n"
        f"• Uptime: {uptime}\n"
        f"• Workers: 500\n\n"
        f"<b>🗄️ Database:</b>\n"
        f"• Status: Connected ✅"
    )
    await message.reply(stats_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="cmd_stats")]]))

@app.on_message(filters.command("stop") & filters.private)
async def cancel_task_cmd(client, message: Message):
    try:
        task_id = message.text.split("_")[1]
        CANCEL_TASKS[task_id] = True
        await message.reply("❌ <b>Process cancellation requested!</b>")
    except Exception:
        await message.reply("❌ Invalid Task ID")

# ------------------- RUN APP ------------------- #

if __name__ == "__main__":
    print("Bot Starting...")
    app.run()
