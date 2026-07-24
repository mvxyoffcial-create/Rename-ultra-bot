import os
import sys
import time
import math
import random
import string
import asyncio
import logging
import psutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    InputMediaPhoto
)
from pyrogram.errors import UserNotParticipant, FloodWait, RPCError

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

if not os.path.exists(config.DOWNLOAD_DIR):
    os.makedirs(config.DOWNLOAD_DIR)

# MongoDB Setup
mongo_client = AsyncIOMotorClient(config.MONGO_URL)
db = mongo_client["rename_bot_db"]
users_col = db["users"]

# Worker & Queue Setup
executor = ThreadPoolExecutor(max_workers=500)
ffmpeg_semaphore = asyncio.Semaphore(10)

# Bot Instance Initialization
bot = Client(
    "RenameBot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=500
)

BOT_START_TIME = time.time()
CANCEL_TASKS = set()
PENDING_INPUTS = {}

PICS_URL = [
    "https://api.aniwallpaper.workers.dev/random?type=girl"
]

WELCOME_STICKER = "CAACAgIAAxkBAAEQZtFpgEdROhGouBVFD3e0K-YjmVHwsgACtCMAAphLKUjeub7NKlvk2TgE"
FALLBACK_WELCOME_IMG = "https://i.ibb.co/pr2H8cwT/img-8312532076.jpg"

START_TXT = """
<b>ʜᴇʏ {}!</b>

<b>ɪ'ᴍ ᴀ ᴘᴏᴡᴇʀғᴜʟ ғɪʟᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ</b> 📝

<b>⚡ ғᴇᴀᴛᴜʀᴇs:</b>
➤ ғɪʟᴇ ʀᴇɴᴀᴍɪɴɢ
➤ ᴍᴇᴛᴀᴅᴀᴛᴀ ᴇᴅɪᴛᴏʀ
➤ sᴛʀᴇᴀᴍ ᴇxᴛʀᴀᴄᴛᴏʀ
➤ ғɪʟᴇ ᴄᴏɴᴠᴇʀᴛᴇʀ
➤ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴛʜᴜᴍʙɴᴀɪʟ
➤ ᴀɴᴅ ᴍᴀɴʏ ᴍᴏʀᴇ...

sᴇɴᴅ ᴀ ғɪʟᴇ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ! 🚀
"""

HELP_TXT = """
<b>✨ ʜᴏᴡ ᴛᴏ ᴜsᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ ✨</b>

<b>📝 ғɪʟᴇ ʀᴇɴᴀᴍɪɴɢ:</b>
• sᴇɴᴅ ᴀɴʏ ғɪʟᴇ/ᴠɪᴅᴇᴏ/ᴀᴜᴅɪᴏ
• ʀᴇᴘʟʏ ᴛᴏ ɪᴛ ᴡɪᴛʜ ɴᴇᴡ ɴᴀᴍᴇ + ᴇxᴛᴇɴsɪᴏɴ
• ʙᴏᴛ ᴡɪʟʟ ʀᴇɴᴀᴍᴇ & sᴇɴᴅ ʙᴀᴄᴋ

<b>📦 ʙᴀᴛᴄʜ ʀᴇɴᴀᴍɪɴɢ:</b>
• sᴇɴᴅ ᴍᴜʟᴛɪᴘʟᴇ ғɪʟᴇs
• ʀᴇᴘʟʏ ᴡɪᴛʜ ᴄᴏᴍᴍᴏɴ ɴᴀᴍᴇ ᴘᴀᴛᴛᴇʀɴ

<b>🖼️ ᴛʜᴜᴍʙɴᴀɪʟ:</b>
/thumbnail - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴛʜᴜᴍʙɴᴀɪʟ
/delthumbnail - ʀᴇᴍᴏᴠᴇ ᴛʜᴜᴍʙɴᴀɪʟ

<b>📋 ᴍᴇᴛᴀᴅᴀᴛᴀ:</b>
/metadata - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴍᴇᴛᴀᴅᴀᴛᴀ
/delmetadata - ʀᴇᴍᴏᴠᴇ ᴍᴇᴛᴀᴅᴀᴛᴀ

<b>✍️ ᴄᴀᴘᴛɪᴏɴ:</b>
/caption - sᴇᴛ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴄᴀᴘᴛɪᴏɴ
/delcaption - ʀᴇᴍᴏᴠᴇ ᴄᴀᴘᴛɪᴏɴ

<b>🏷️ ᴘʀᴇғɪx/sᴜғғɪx:</b>
/prefix - ᴀᴅᴅ ᴘʀᴇғɪx ᴛᴏ ғɪʟᴇɴᴀᴍᴇ
/suffix - ᴀᴅᴅ sᴜғғɪx ᴛᴏ ғɪʟᴇɴᴀᴍᴇ

<b>🎬 ᴍᴇᴅɪᴀ ᴛᴏᴏʟs:</b>
• sᴛʀᴇᴀᴍ ᴇxᴛʀᴀᴄᴛᴏʀ
• ᴀᴜᴅɪᴏ ᴇxᴛʀᴀᴄᴛᴏʀ
• sᴜʙᴛɪᴛʟᴇ ᴇxᴛʀᴀᴄᴛᴏʀ
• ᴀᴜᴅɪᴏ/ᴠɪᴅᴇᴏ ᴄᴏɴᴠᴇʀᴛᴇʀ
• sᴄʀᴇᴇɴsʜᴏᴛ ɢᴇɴᴇʀᴀᴛᴏʀ
• sᴀᴍᴘʟᴇ ᴠɪᴅᴇᴏ ᴄʀᴇᴀᴛᴏʀ
• ᴄᴏᴍᴘʀᴇssɪᴏɴ

<b>ℹ️ ᴏᴛʜᴇʀ ᴄᴏᴍᴍᴀɴᴅs:</b>
/info - ʏᴏᴜʀ ᴅᴇᴛᴀɪʟs ᴡɪᴛʜ ᴘᴏғɪʟᴇ ᴘʜᴏᴛᴏ
/settings - ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs
/ping - ᴄʜᴇᴄᴋ ʙᴏᴛ ʀᴇsᴘᴏɴsᴇ ᴛɪᴍᴇ
/about - ʙᴏᴛ ɪɴғᴏʀᴍᴀᴛɪᴏɴ

<b>👑 ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅs:</b>
/stats - ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs
/broadcast - ᴍᴇssᴀɢᴇ ᴀʟʟ ᴜsᴇʀs
/ban - ʙᴀɴ ᴀ ᴜsᴇʀ
/unban - ᴜɴʙᴀɴ ᴀ ᴜsᴇʀ
"""

ABOUT_TXT = """
<b>╭────[ ᴍʏ ᴅᴇᴛᴀɪʟs ]────⍟

├⍟ Mʏ Nᴀᴍᴇ : {}

├⍟ Dᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👨‍💻

├⍟ Oᴡɴᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👑

├⍟ Lɪʙʀᴀʀʏ : <a href='https://github.com/pyrogram/pyrogram'>ᴘʏʀᴏɢʀᴀᴍ ᴠ2</a> 📚

├⍟ Lᴀɴɢᴜᴀɢᴇ : <a href='https://www.python.org/'>ᴘʏᴛʜᴏɴ 3</a> 🐍

├⍟ Dᴀᴛᴀʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a> 🍃

├⍟ SᴇʀᴠᴇR : ᴅᴇᴅɪᴄᴀᴛᴇᴅ ᴠᴘs ☁️

├⍟ Fᴇᴀᴛᴜʀᴇ : ғɪʟᴇ ʀᴇɴᴀᴍᴇʀ 📝

├⍟ Wᴏʀᴋᴇʀs : 500 ᴄᴏɴᴄᴜʀʀᴇɴᴛ ⚡

├⍟ Bᴜɪʟᴅ Sᴛᴀᴛᴜs : ᴠ2.0 [ ᴜʟᴛʀᴀ ] 🚀

╰───────────────⍟</b>
"""

def humanbytes(size):
    if not size:
        return "0B"
    power = 2**10
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
           (f"{seconds}s, " if seconds else ""))
    return tmp[:-2] if tmp else "0s"

def get_random_mix_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

async def get_user(user_id):
    user = await users_col.find_one({"user_id": int(user_id)})
    if not user:
        user_data = {
            "user_id": int(user_id),
            "first_name": "",
            "last_name": "",
            "username": "",
            "thumbnail": None,
            "metadata_title": None,
            "metadata_artist": None,
            "metadata_album": None,
            "metadata_year": None,
            "caption": None,
            "prefix": None,
            "suffix": None,
            "banned": False,
            "joined_date": datetime.now(),
            "last_active": datetime.now(),
            "total_processed": 0
        }
        await users_col.insert_one(user_data)
        return user_data
    return user

async def update_user(user_id, data):
    await users_col.update_one({"user_id": int(user_id)}, {"$set": data})

async def check_force_sub(client, user_id):
    not_joined = []
    for channel in config.FORCE_SUB_CHANNELS:
        try:
            member = await client.get_chat_member(f"@{channel}", user_id)
            if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
                not_joined.append(channel)
        except Exception:
            not_joined.append(channel)
    
    if not_joined:
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"📢 Join @{ch}", url=f"https://t.me/{ch}")])
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify_sub")])
        return False, InlineKeyboardMarkup(buttons)
    return True, None

async def is_banned(user_id):
    user = await get_user(user_id)
    return user.get("banned", False)

# Koyeb Health-Check HTTP Endpoint
async def handle_koyeb_healthcheck(request):
    return web.Response(text="Bot running cleanly!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_koyeb_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logger.info(f"Health check web server running on port {config.PORT}")

# Progress Bar Engine
async def progress_for_pyrogram(current, total, ud_type, message, start, task_id):
    if task_id in CANCEL_TASKS:
        bot.stop_transmission()
        return

    now = time.time()
    diff = now - start
    if diff == 0:
        return

    if round(diff % 2.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000

        elapsed_time = time_formatter(elapsed_time)

        progress = "[{0}{1}]".format(
            ''.join(["█" for _ in range(math.floor(percentage / 5))]),
            ''.join(["░" for _ in range(20 - math.floor(percentage / 5))])
        )

        tmp = (
            f"╔════════════════════════════════════╗\n"
            f"║ 📁 {ud_type}\n"
            f"║\n"
            f"║ {progress} {round(percentage, 2)}%\n"
            f"║\n"
            f"║ ⬇️ Speed: {humanbytes(speed)}/s\n"
            f"║ 📦 {humanbytes(current)} / {humanbytes(total)}\n"
            f"║ ⏳ ETA: {time_formatter(time_to_completion)}\n"
            f"║\n"
            f"║ 🚀 Powered by @Venuboyy\n"
            f"╚════════════════════════════════════╝"
        )
        try:
            await message.edit(
                text=tmp,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_process_{task_id}")]
                ])
            )
        except Exception:
            pass

# Main Menu Keyboards
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Rename", callback_data="btn_rename"), InlineKeyboardButton("📦 Batch Rename", callback_data="btn_batch")],
        [InlineKeyboardButton("🖼️ Thumbnail", callback_data="btn_thumb"), InlineKeyboardButton("📋 Metadata", callback_data="btn_meta")],
        [InlineKeyboardButton("✍️ Caption", callback_data="btn_caption"), InlineKeyboardButton("🏷️ Prefix/Suffix", callback_data="btn_prefix")],
        [InlineKeyboardButton("🎬 Media Tools", callback_data="btn_media"), InlineKeyboardButton("🔄 Convert", callback_data="btn_convert")],
        [InlineKeyboardButton("📸 Screenshot", callback_data="btn_ss"), InlineKeyboardButton("🎵 Audio Tools", callback_data="btn_audio")],
        [InlineKeyboardButton("📋 Subtitle", callback_data="btn_sub"), InlineKeyboardButton("🗜️ Compress", callback_data="btn_compress")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="btn_settings"), InlineKeyboardButton("ℹ️ About", callback_data="btn_about")],
        [InlineKeyboardButton("📊 Stats", callback_data="btn_stats"), InlineKeyboardButton("🔧 Help", callback_data="btn_help")]
    ])

@bot.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")
        
    await get_user(user_id)
    await update_user(user_id, {
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name or "",
        "username": message.from_user.username or "",
        "last_active": datetime.now()
    })

    try:
        stk = await message.reply_sticker(WELCOME_STICKER)
        await asyncio.sleep(2)
        await stk.delete()
    except Exception:
        pass

    welcome_img = f"{random.choice(PICS_URL)}?r={get_random_mix_id()}"
    caption = START_TXT.format(message.from_user.mention)
    
    try:
        await message.reply_photo(photo=welcome_img, caption=caption, reply_markup=get_main_menu_keyboard())
    except Exception:
        await message.reply_photo(photo=FALLBACK_WELCOME_IMG, caption=caption, reply_markup=get_main_menu_keyboard())

@bot.on_message(filters.command("help"))
async def help_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    await message.reply(HELP_TXT, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Home", callback_data="home_menu")]
    ]))

@bot.on_message(filters.command("about"))
async def about_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    bot_info = await client.get_me()
    await message.reply(ABOUT_TXT.format(bot_info.first_name), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Home", callback_data="home_menu")]
    ]), disable_web_page_preview=True)

@bot.on_message(filters.command("info"))
async def info_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    u_data = await get_user(user_id)
    photos = [p async for p in client.get_chat_photos(user_id, limit=1)]
    
    caption = f"""
<b>📋 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>

➲ <b>First Name:</b> {message.from_user.first_name}
➲ <b>Last Name:</b> {message.from_user.last_name or 'N/A'}
➲ <b>Telegram ID:</b> <code>{user_id}</code>
➲ <b>Data Centre:</b> {message.from_user.dc_id or 'Unknown'}
➲ <b>User Name:</b> @{message.from_user.username or 'N/A'}
➲ <b>User 𝖫𝗂𝗇𝗄:</b> <a href='tg://user?id={user_id}'>Click Here</a>

<b>📊 Bot Usage:</b>
➲ <b>Files Processed:</b> {u_data.get('total_processed', 0)}
➲ <b>Joined:</b> {u_data.get('joined_date', datetime.now()).strftime('%Y-%m-%d')}
"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ])

    if photos:
        await message.reply_photo(photo=photos[0].file_id, caption=caption, reply_markup=kb)
    else:
        await message.reply(text=caption, reply_markup=kb)

@bot.on_message(filters.command("settings"))
async def settings_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    u_data = await get_user(user_id)
    
    thumb_status = "✅ Set" if u_data.get("thumbnail") else "❌ Not Set"
    meta_status = "✅ Set" if u_data.get("metadata_title") else "❌ Not Set"
    cap_status = "✅ Set" if u_data.get("caption") else "❌ Not Set"
    prefix = u_data.get("prefix") or "None"
    suffix = u_data.get("suffix") or "None"
    
    txt = f"""
<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>

🖼️ <b>Thumbnail:</b> {thumb_status}
📋 <b>Metadata:</b> {meta_status}
✍️ <b>Caption:</b> {cap_status}
🏷️ <b>Prefix:</b> <code>{prefix}</code>
🏷️ <b>Suffix:</b> <code>{suffix}</code>

<b>📊 Stats:</b>
📁 <b>Files Processed:</b> {u_data.get('total_processed', 0)}
"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb_cb"), InlineKeyboardButton("🗑️ Del Thumbnail", callback_data="del_thumb_cb")],
        [InlineKeyboardButton("📋 Set Metadata", callback_data="set_meta_cb"), InlineKeyboardButton("🗑️ Del Metadata", callback_data="del_meta_cb")],
        [InlineKeyboardButton("✍️ Set Caption", callback_data="set_cap_cb"), InlineKeyboardButton("🗑️ Del Caption", callback_data="del_cap_cb")],
        [InlineKeyboardButton("🏷️ Prefix", callback_data="set_prefix_cb"), InlineKeyboardButton("🏷️ Suffix", callback_data="set_suffix_cb")],
        [InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ])
    await message.reply(txt, reply_markup=kb)

@bot.on_message(filters.command("thumbnail"))
async def thumbnail_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    if message.reply_to_message and message.reply_to_message.photo:
        file_id = message.reply_to_message.photo.file_id
        await update_user(user_id, {"thumbnail": file_id})
        await message.reply("✅ <b>Permanent thumbnail set successfully!</b>", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))
    else:
        PENDING_INPUTS[user_id] = "SET_THUMBNAIL"
        await message.reply("🖼️ <b>Please send the photo you want to set as custom thumbnail.</b>")

@bot.on_message(filters.command("delthumbnail"))
async def delthumbnail_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    await update_user(user_id, {"thumbnail": None})
    await message.reply("🗑️ <b>Thumbnail removed successfully!</b>", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Set New", callback_data="set_thumb_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ]))

@bot.on_message(filters.command("metadata"))
async def metadata_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    PENDING_INPUTS[user_id] = "SET_METADATA"
    await message.reply("📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>\n\nExample: <code>My Video | John Doe | My Album | 2024</code>")

@bot.on_message(filters.command("delmetadata"))
async def delmetadata_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    await update_user(user_id, {
        "metadata_title": None,
        "metadata_artist": None,
        "metadata_album": None,
        "metadata_year": None
    })
    await message.reply("🗑️ <b>Metadata removed!</b>", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Set New", callback_data="set_meta_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ]))

@bot.on_message(filters.command("caption"))
async def caption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    PENDING_INPUTS[user_id] = "SET_CAPTION"
    await message.reply("✍️ <b>Send your permanent caption text</b>\n\nYou can use:\n• <code>{filename}</code> - for file name\n• <code>{size}</code> - for file size\n• HTML formatting allowed")

@bot.on_message(filters.command("delcaption"))
async def delcaption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    await update_user(user_id, {"caption": None})
    await message.reply("🗑️ <b>Caption removed!</b>", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Set New", callback_data="set_cap_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ]))

@bot.on_message(filters.command("prefix"))
async def prefix_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    PENDING_INPUTS[user_id] = "SET_PREFIX"
    await message.reply("🏷️ <b>Send prefix text to add BEFORE filename</b>\n\nExample: <code>[TeamName]_</code>\nResult: <code>[TeamName]_filename.mkv</code>")

@bot.on_message(filters.command("suffix"))
async def suffix_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    PENDING_INPUTS[user_id] = "SET_SUFFIX"
    await message.reply("🏷️ <b>Send suffix text to add AFTER filename</b>\n\nExample: <code>@TeamName</code>\nResult: <code>filename@TeamName.mkv</code>")

@bot.on_message(filters.command("ping"))
async def ping_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    start = time.time()
    msg = await message.reply("🏓 <b>Pinging...</b>")
    end = time.time()
    ping_time = round((end - start) * 1000, 2)
    uptime = time_formatter((time.time() - BOT_START_TIME) * 1000)

    txt = f"""
<b>🏓 ᴘᴏɴɢ!</b>

<b>⏱️ Response Time:</b> <code>{ping_time}ms</code>
<b>📡 Bot Uptime:</b> <code>{uptime}</code>
<b>⚡ Workers:</b> <code>500</code>
<b>🗄️ Database:</b> <code>Connected ✅</code>
"""
    await msg.edit(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_ping"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ]))

# Admin Handlers
@bot.on_message(filters.command("stats") & filters.user(config.ADMIN_IDS))
async def stats_cmd(client: Client, message: Message):
    total_users = await users_col.count_documents({})
    banned_count = await users_col.count_documents({"banned": True})
    
    cpu_percent = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = time_formatter((time.time() - BOT_START_TIME) * 1000)

    txt = f"""
<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>

<b>👥 Users:</b>
• Total: {total_users}
• Active Today: {total_users}
• Banned: {banned_count}

<b>💻 System:</b>
• CPU: {cpu_percent}%
• RAM: {humanbytes(ram.used)}/{humanbytes(ram.total)} ({ram.percent}%)
• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)

<b>⚡ Bot:</b>
• Uptime: {uptime}
• Workers: 500
• Queue: Active

<b>🗄️ Database:</b>
• Status: Connected ✅
• Database: MongoDB
"""
    await message.reply(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats"), InlineKeyboardButton("📨 Broadcast", callback_data="admin_bc"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
    ]))

@bot.on_message(filters.command("broadcast") & filters.user(config.ADMIN_IDS))
async def broadcast_cmd(client: Client, message: Message):
    if not message.reply_to_message:
        PENDING_INPUTS[message.from_user.id] = "BROADCAST_MSG"
        return await message.reply("📨 <b>Send or reply to a message you want to broadcast to all users.</b>")

    target_msg = message.reply_to_message
    total_users = await users_col.count_documents({})
    
    await message.reply(
        f"📨 <b>Broadcast Preview Ready.</b>\nSend to {total_users} users?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Send", callback_data="confirm_bc"), InlineKeyboardButton("❌ Cancel", callback_data="cancel_bc")]
        ])
    )
    PENDING_INPUTS[f"bc_msg_{message.from_user.id}"] = target_msg

@bot.on_message(filters.command("ban") & filters.user(config.ADMIN_IDS))
async def ban_cmd(client: Client, message: Message):
    try:
        target_id = int(message.command[1])
        await update_user(target_id, {"banned": True})
        await message.reply(f"🚫 <b>User {target_id} banned!</b>")
        try:
            await client.send_message(target_id, "⚠️ <b>You have been banned from using this bot!</b>")
        except Exception:
            pass
    except Exception:
        await message.reply("<b>Error:</b> Provide a valid user ID.\nUsage: <code>/ban 12345678</code>")

@bot.on_message(filters.command("unban") & filters.user(config.ADMIN_IDS))
async def unban_cmd(client: Client, message: Message):
    try:
        target_id = int(message.command[1])
        await update_user(target_id, {"banned": False})
        await message.reply(f"✅ <b>User {target_id} unbanned!</b>")
        try:
            await client.send_message(target_id, "✅ <b>You have been unbanned! You can use the bot now.</b>")
        except Exception:
            pass
    except Exception:
        await message.reply("<b>Error:</b> Provide a valid user ID.\nUsage: <code>/unban 12345678</code>")

# Text inputs handler
@bot.on_message(filters.private & filters.text & ~filters.command(["start", "help", "about", "info", "settings", "thumbnail", "delthumbnail", "metadata", "delmetadata", "caption", "prefix", "suffix", "ping", "stats", "broadcast", "ban", "unban"]))
async def text_input_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    state = PENDING_INPUTS.get(user_id)
    if not state:
        return

    if state == "SET_METADATA":
        try:
            parts = [p.strip() for p in message.text.split("|")]
            title = parts[0] if len(parts) > 0 else "Unknown"
            artist = parts[1] if len(parts) > 1 else "Unknown"
            album = parts[2] if len(parts) > 2 else "Unknown"
            year = parts[3] if len(parts) > 3 else "2024"

            await update_user(user_id, {
                "metadata_title": title,
                "metadata_artist": artist,
                "metadata_album": album,
                "metadata_year": year
            })
            del PENDING_INPUTS[user_id]
            await message.reply(f"✅ <b>Metadata set:</b>\n• Title: {title}\n• Artist: {artist}\n• Album: {album}\n• Year: {year}", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Remove", callback_data="del_meta_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
            ]))
        except Exception:
            await message.reply("❌ Invalid format! Use: <code>Title | Artist | Album | Year</code>")

    elif state == "SET_CAPTION":
        await update_user(user_id, {"caption": message.text})
        del PENDING_INPUTS[user_id]
        await message.reply(f"✅ <b>Caption updated successfully!</b>\n\n<b>Preview:</b>\n{message.text}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Remove", callback_data="del_cap_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))

    elif state == "SET_PREFIX":
        await update_user(user_id, {"prefix": message.text})
        del PENDING_INPUTS[user_id]
        await message.reply(f"✅ <b>Prefix set:</b> <code>{message.text}</code>", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Remove", callback_data="set_prefix_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))

    elif state == "SET_SUFFIX":
        await update_user(user_id, {"suffix": message.text})
        del PENDING_INPUTS[user_id]
        await message.reply(f"✅ <b>Suffix set:</b> <code>{message.text}</code>", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Remove", callback_data="set_suffix_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))

@bot.on_message(filters.private & filters.photo)
async def photo_input_handler(client: Client, message: Message):
    user_id = message.from_user.id
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    state = PENDING_INPUTS.get(user_id)
    if state == "SET_THUMBNAIL":
        file_id = message.photo.file_id
        await update_user(user_id, {"thumbnail": file_id})
        del PENDING_INPUTS[user_id]
        await message.reply("✅ <b>Permanent thumbnail set successfully!</b>", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb_cb"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))

# File Receiver Command Handler
@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def file_receiver_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    media = message.document or message.video or message.audio
    filename = getattr(media, "file_name", "Unknown_File")
    filesize = humanbytes(getattr(media, "file_size", 0))
    duration = time_formatter(getattr(media, "duration", 0) * 1000) if hasattr(media, "duration") else "N/A"

    u_data = await get_user(user_id)
    await update_user(user_id, {"total_processed": u_data.get("total_processed", 0) + 1})

    txt = f"""
<b>📁 File Received!</b>

📄 <b>Name:</b> <code>{filename}</code>
📦 <b>Size:</b> {filesize}
⏱️ <b>Duration:</b> {duration}

<b>✏️ Reply to this message with new filename + extension</b>
Example: <code>My New Video.mkv</code>

<b>🔧 Available formats:</b>
Video: .mp4, .mkv, .avi, .mov, .webm, .ts
Audio: .mp3, .aac, .m4a, .flac, .opus
"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Media Tools", callback_data=f"tools_{message.id}"), InlineKeyboardButton("🔄 Convert", callback_data=f"conv_{message.id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
    await message.reply(txt, reply_markup=kb, reply_to_message_id=message.id)

# File Renamer Processing Implementation
@bot.on_message(filters.private & filters.reply & ~filters.command(["start", "help", "about", "info", "settings"]))
async def rename_reply_processor(client: Client, message: Message):
    user_id = message.from_user.id
    
    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)
    if await is_banned(user_id):
        return await message.reply("⚠️ You have been banned from using this bot!")

    replied = message.reply_to_message
    if not replied or not (replied.document or replied.video or replied.audio or replied.text):
        return

    if replied.from_user.id == (await client.get_me()).id and "File Received!" in (replied.text or ""):
        original_media_msg = replied.reply_to_message
        if not original_media_msg:
            return await message.reply("❌ Original media file reference not found.")

        new_name = message.text.strip()
        u_data = await get_user(user_id)

        prefix = u_data.get("prefix") or ""
        suffix = u_data.get("suffix") or ""

        if prefix or suffix:
            name_part, ext_part = os.path.splitext(new_name)
            new_name = f"{prefix}{name_part}{suffix}{ext_part}"

        task_id = get_random_mix_id()
        status_msg = await message.reply("⚡ <b>Starting file download...</b>")

        input_path = os.path.join(config.DOWNLOAD_DIR, f"{task_id}_input")
        output_path = os.path.join(config.DOWNLOAD_DIR, new_name)

        try:
            start_time = time.time()
            
            dl_path = None
            for attempt in range(3):
                try:
                    dl_path = await client.download_media(
                        message=original_media_msg,
                        file_name=input_path,
                        progress=progress_for_pyrogram,
                        progress_args=("Downloading...", status_msg, start_time, task_id)
                    )
                    if dl_path:
                        break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(2)

            if task_id in CANCEL_TASKS:
                await status_msg.edit("❌ <b>Process cancelled!</b>")
                return

            await status_msg.edit("⚙️ <b>Processing file metadata and transformations...</b>")

            async with ffmpeg_semaphore:
                meta_title = u_data.get("metadata_title")
                cmd = ["ffmpeg", "-y", "-i", dl_path]

                if meta_title:
                    cmd.extend([
                        "-metadata", f"title={meta_title}",
                        "-metadata", f"artist={u_data.get('metadata_artist') or ''}",
                        "-metadata", f"album={u_data.get('metadata_album') or ''}",
                        "-metadata", f"date={u_data.get('metadata_year') or ''}"
                    ])

                cmd.extend(["-c", "copy", output_path])

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

            thumb_path = None
            if u_data.get("thumbnail"):
                try:
                    thumb_path = await client.download_media(u_data.get("thumbnail"), file_name=os.path.join(config.DOWNLOAD_DIR, f"{task_id}_thumb.jpg"))
                except Exception:
                    thumb_path = None

            caption_template = u_data.get("caption")
            if caption_template:
                caption = caption_template.format(
                    filename=new_name,
                    size=humanbytes(os.path.getsize(output_path))
                )
            else:
                caption = f"<b>{new_name}</b>"

            await status_msg.edit("⚡ <b>Starting file upload...</b>")
            upload_start = time.time()

            for attempt in range(3):
                try:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=output_path,
                        thumb=thumb_path,
                        caption=caption,
                        progress=progress_for_pyrogram,
                        progress_args=("Uploading...", status_msg, upload_start, task_id)
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(2)

            await status_msg.delete()

        except Exception as e:
            logger.error(f"Execution Error: {e}")
            await status_msg.edit(f"❌ <b>An error occurred:</b> <code>{str(e)}</code>")

        finally:
            CANCEL_TASKS.discard(task_id)
            for file_target in [input_path, output_path, dl_path, os.path.join(config.DOWNLOAD_DIR, f"{task_id}_thumb.jpg")]:
                if file_target and os.path.exists(file_target):
                    try:
                        os.remove(file_target)
                    except Exception:
                        pass

# Fully Wire Up ALL Inline Keyboard Callback Actions

@bot.on_callback_query()
async def callback_route_handler(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    # Always answer queries to keep inline buttons active and operational
    await query.answer()

    if data == "verify_sub":
        is_joined, markup = await check_force_sub(client, user_id)
        if is_joined:
            await query.answer("✅ Verified! You can now use the bot.", show_alert=True)
            try:
                await query.message.delete()
            except Exception:
                pass
        else:
            await query.answer("❌ Please join all channels first!", show_alert=True)
        return

    is_joined, markup = await check_force_sub(client, user_id)
    if not is_joined:
        return await query.message.reply("⚠️ You must join our channels to use this bot!", reply_markup=markup)

    if await is_banned(user_id):
        return await query.answer("⚠️ You are banned from using this bot!", show_alert=True)

    # Core Navigation & Button Action Routes
    if data == "home_menu":
        welcome_img = f"{random.choice(PICS_URL)}?r={get_random_mix_id()}"
        caption = START_TXT.format(query.from_user.mention)
        try:
            await query.message.edit_media(
                media=InputMediaPhoto(welcome_img, caption=caption),
                reply_markup=get_main_menu_keyboard()
            )
        except Exception:
            await query.message.edit_text(text=caption, reply_markup=get_main_menu_keyboard())

    elif data in ["btn_rename", "btn_batch"]:
        await query.message.reply("📝 <b>Send any file or video to begin renaming or batch processing.</b>")

    elif data == "btn_thumb":
        u_data = await get_user(user_id)
        status = "✅ Permanent Thumbnail is Set!" if u_data.get("thumbnail") else "❌ No Custom Thumbnail Set."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb_cb"), InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb_cb")],
            [InlineKeyboardButton("🏠 Back", callback_data="home_menu")]
        ])
        await query.message.edit_text(f"<b>🖼️ Custom Thumbnail Manager</b>\n\nStatus: <b>{status}</b>", reply_markup=kb)

    elif data == "btn_meta":
        PENDING_INPUTS[user_id] = "SET_METADATA"
        await query.message.reply("📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>")

    elif data == "btn_caption":
        PENDING_INPUTS[user_id] = "SET_CAPTION"
        await query.message.reply("✍️ <b>Send custom caption template:</b>")

    elif data == "btn_prefix":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏷️ Set Prefix", callback_data="set_prefix_cb"), InlineKeyboardButton("🏷️ Set Suffix", callback_data="set_suffix_cb")],
            [InlineKeyboardButton("🏠 Back", callback_data="home_menu")]
        ])
        await query.message.edit_text("<b>🏷️ Configure Prefix & Suffix Configuration:</b>", reply_markup=kb)

    elif data == "btn_help":
        await query.message.edit_text(HELP_TXT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Back to Home", callback_data="home_menu")]
        ]))

    elif data == "btn_about":
        bot_info = await client.get_me()
        await query.message.edit_text(ABOUT_TXT.format(bot_info.first_name), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Back to Home", callback_data="home_menu")]
        ]), disable_web_page_preview=True)

    elif data in ["btn_stats", "refresh_stats"]:
        total_users = await users_col.count_documents({})
        banned_count = await users_col.count_documents({"banned": True})
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        uptime = time_formatter((time.time() - BOT_START_TIME) * 1000)

        txt = f"""
<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>

<b>👥 Users:</b>
• Total: {total_users}
• Banned: {banned_count}

<b>💻 System:</b>
• CPU: {cpu_percent}%
• RAM: {humanbytes(ram.used)}/{humanbytes(ram.total)} ({ram.percent}%)
• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)

<b>⚡ Bot Status:</b>
• Uptime: {uptime}
• Workers: 500
• Database: Connected ✅
"""
        await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats"), InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ]))

    elif data == "btn_settings":
        u_data = await get_user(user_id)
        thumb_status = "✅ Set" if u_data.get("thumbnail") else "❌ Not Set"
        meta_status = "✅ Set" if u_data.get("metadata_title") else "❌ Not Set"
        cap_status = "✅ Set" if u_data.get("caption") else "❌ Not Set"
        prefix = u_data.get("prefix") or "None"
        suffix = u_data.get("suffix") or "None"
        
        txt = f"""
<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>

🖼️ <b>Thumbnail:</b> {thumb_status}
📋 <b>Metadata:</b> {meta_status}
✍️ <b>Caption:</b> {cap_status}
🏷️ <b>Prefix:</b> <code>{prefix}</code>
🏷️ <b>Suffix:</b> <code>{suffix}</code>

<b>📊 Stats:</b>
📁 <b>Files Processed:</b> {u_data.get('total_processed', 0)}
"""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb_cb"), InlineKeyboardButton("🗑️ Del Thumbnail", callback_data="del_thumb_cb")],
            [InlineKeyboardButton("📋 Set Metadata", callback_data="set_meta_cb"), InlineKeyboardButton("🗑️ Del Metadata", callback_data="del_meta_cb")],
            [InlineKeyboardButton("✍️ Set Caption", callback_data="set_cap_cb"), InlineKeyboardButton("🗑️ Del Caption", callback_data="del_cap_cb")],
            [InlineKeyboardButton("🏷️ Prefix", callback_data="set_prefix_cb"), InlineKeyboardButton("🏷️ Suffix", callback_data="set_suffix_cb")],
            [InlineKeyboardButton("🏠 Home", callback_data="home_menu")]
        ])
        await query.message.edit_text(txt, reply_markup=kb)

    elif data in ["btn_media", "btn_ss", "btn_audio", "btn_sub", "btn_compress"]:
        txt = "<b>🎬 Media Tools Menu</b>\n\nSelect desired operational action:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Stream Extract", callback_data="tool_streamext"), InlineKeyboardButton("🚫 Stream Remove", callback_data="tool_streamrem")],
            [InlineKeyboardButton("🎵 Audio Extract", callback_data="tool_audioext"), InlineKeyboardButton("🔇 Audio Remove", callback_data="tool_audiorem")],
            [InlineKeyboardButton("📋 Sub Extract", callback_data="tool_subext"), InlineKeyboardButton("🗑️ Sub Remove", callback_data="tool_subrem")],
            [InlineKeyboardButton("📸 Screenshot", callback_data="tool_ss"), InlineKeyboardButton("✂️ Sample Video", callback_data="tool_sample")],
            [InlineKeyboardButton("🏠 Back", callback_data="home_menu")]
        ])
        if query.message.photo:
            await query.message.delete()
            await query.message.reply(txt, reply_markup=kb)
        else:
            await query.message.edit_text(txt, reply_markup=kb)

    elif data == "btn_convert":
        txt = "<b>🔄 Format Conversion Menu</b>\n\nSelect target media format:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("MP4", callback_data="conv_mp4"), InlineKeyboardButton("MKV", callback_data="conv_mkv"), InlineKeyboardButton("AVI", callback_data="conv_avi")],
            [InlineKeyboardButton("MOV", callback_data="conv_mov"), InlineKeyboardButton("WEBM", callback_data="conv_webm"), InlineKeyboardButton("TS", callback_data="conv_ts")],
            [InlineKeyboardButton("MP3", callback_data="conv_mp3"), InlineKeyboardButton("AAC", callback_data="conv_aac"), InlineKeyboardButton("M4A", callback_data="conv_m4a")],
            [InlineKeyboardButton("FLAC", callback_data="conv_flac"), InlineKeyboardButton("OPUS", callback_data="conv_opus")],
            [InlineKeyboardButton("🏠 Back", callback_data="home_menu")]
        ])
        if query.message.photo:
            await query.message.delete()
            await query.message.reply(txt, reply_markup=kb)
        else:
            await query.message.edit_text(txt, reply_markup=kb)

    # Sub-action Handlers
    elif data.startswith("tool_") or data.startswith("conv_"):
        await query.message.reply("📁 <b>Reply to any existing uploaded video or file to perform this selected action.</b>")

    elif data == "set_thumb_cb":
        PENDING_INPUTS[user_id] = "SET_THUMBNAIL"
        await query.message.reply("🖼️ <b>Send photo to set as custom permanent thumbnail.</b>")

    elif data == "del_thumb_cb":
        await update_user(user_id, {"thumbnail": None})
        await query.answer("🗑️ Thumbnail removed!", show_alert=True)

    elif data == "set_meta_cb":
        PENDING_INPUTS[user_id] = "SET_METADATA"
        await query.message.reply("📋 <b>Send metadata format:</b>\n<code>Title | Artist | Album | Year</code>")

    elif data == "del_meta_cb":
        await update_user(user_id, {"metadata_title": None, "metadata_artist": None, "metadata_album": None, "metadata_year": None})
        await query.answer("🗑️ Metadata reset!", show_alert=True)

    elif data == "set_cap_cb":
        PENDING_INPUTS[user_id] = "SET_CAPTION"
        await query.message.reply("✍️ <b>Send custom caption format:</b>")

    elif data == "del_cap_cb":
        await update_user(user_id, {"caption": None})
        await query.answer("🗑️ Caption reset!", show_alert=True)

    elif data == "set_prefix_cb":
        PENDING_INPUTS[user_id] = "SET_PREFIX"
        await query.message.reply("🏷️ <b>Send custom prefix:</b>")

    elif data == "set_suffix_cb":
        PENDING_INPUTS[user_id] = "SET_SUFFIX"
        await query.message.reply("🏷️ <b>Send custom suffix:</b>")

    elif data.startswith("cancel_process_"):
        task_id = data.split("_")[2]
        CANCEL_TASKS.add(task_id)
        await query.answer("❌ Cancelling operation...", show_alert=True)

    elif data == "cancel_action":
        try:
            await query.message.delete()
        except Exception:
            pass

    # Admin Broadcast Callback Confirmations
    elif data == "confirm_bc" and user_id in config.ADMIN_IDS:
        target_msg = PENDING_INPUTS.get(f"bc_msg_{user_id}")
        if not target_msg:
            return await query.message.edit_text("❌ Broadcast message target missing.")

        await query.message.edit_text("📨 <b>Broadcasting message to all users...</b>")
        users = users_col.find({})
        sent, failed = 0, 0
        total_users = await users_col.count_documents({})

        async for u in users:
            try:
                await target_msg.copy(chat_id=u["user_id"])
                sent += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await target_msg.copy(chat_id=u["user_id"])
                sent += 1
            except Exception:
                failed += 1

            if (sent + failed) % 20 == 0:
                try:
                    await query.message.edit_text(f"📨 <b>Broadcasting...</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}\n📊 Progress: {sent + failed}/{total_users}")
                except Exception:
                    pass

        await query.message.edit_text(f"✅ <b>Broadcast Complete!</b>\n\n✅ Successful: {sent}\n❌ Failed: {failed}")
        PENDING_INPUTS.pop(f"bc_msg_{user_id}", None)

    elif data == "cancel_bc":
        PENDING_INPUTS.pop(f"bc_msg_{user_id}", None)
        await query.message.edit_text("❌ Broadcast cancelled.")

# Core Launch Async Initialization
async def main():
    await start_web_server()
    await bot.start()
    logger.info("Bot successfully deployed and active!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
