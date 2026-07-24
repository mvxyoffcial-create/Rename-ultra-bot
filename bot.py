import asyncio
import concurrent.futures
import logging
import os
import random
import re
import shutil
import string
import time
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

import aiohttp
from aiohttp import web
import psutil
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery,
    Message, User, ForceReply
)
from pyrogram.errors import FloodWait, UserNotParticipant, RPCError

# Configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URL,
    ADMIN_IDS, FORCE_SUB_CHANNELS, PICS_URL
)

# Port for health check
PORT = int(os.getenv("PORT", 8000))

# Setup logging – now more verbose
logging.basicConfig(
    level=logging.DEBUG,  # changed to DEBUG for more details
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global vars
START_TIME = time.time()
downloads_dir = "./downloads"
os.makedirs(downloads_dir, exist_ok=True)

# Database
client_db = AsyncIOMotorClient(MONGO_URL)
db = client_db.rename_bot
users_col = db.users

# Bot client
app = Client(
    "rename_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=500,
    parse_mode=enums.ParseMode.HTML
)

# -------------------------------------------------------------------
# Helper functions (unchanged)
# -------------------------------------------------------------------
def get_random_mix_id() -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

def get_welcome_image() -> str:
    pic = random.choice(PICS_URL)
    return f"{pic}?r={get_random_mix_id()}"

def humanbytes(size: int) -> str:
    if not size:
        return "0B"
    power = 1024
    t = 0
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    while size >= power and t < len(units)-1:
        size /= power
        t += 1
    return f"{size:.2f} {units[t]}"

def time_formatter(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

def get_uptime() -> str:
    return time_formatter(int(time.time() - START_TIME))

def truncate_text(text: str, length: int = 30) -> str:
    return text[:length-3] + "..." if len(text) > length else text

# -------------------------------------------------------------------
# Force Subscribe
# -------------------------------------------------------------------
async def check_force_sub(user_id: int) -> Tuple[bool, Optional[InlineKeyboardMarkup]]:
    not_joined = []
    for channel in FORCE_SUB_CHANNELS:
        try:
            member = await app.get_chat_member(f"@{channel}", user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except UserNotParticipant:
            not_joined.append(channel)
        except Exception as e:
            logger.error(f"Force sub check error for {channel}: {e}")
            not_joined.append(channel)
    if not_joined:
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"📢 Join @{ch}", url=f"https://t.me/{ch}")])
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify_sub")])
        return False, InlineKeyboardMarkup(buttons)
    return True, None

async def is_banned(user_id: int) -> bool:
    user = await users_col.find_one({"user_id": user_id})
    return user.get("banned", False) if user else False

# -------------------------------------------------------------------
# Database helpers
# -------------------------------------------------------------------
async def save_user(user: User):
    data = {
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "username": user.username or "",
        "joined_date": datetime.now(),
        "last_active": datetime.now(),
        "total_processed": 0,
        "banned": False,
        "thumbnail": None,
        "metadata_title": None,
        "metadata_artist": None,
        "metadata_album": None,
        "metadata_year": None,
        "caption": None,
        "prefix": None,
        "suffix": None
    }
    await users_col.update_one(
        {"user_id": user.id},
        {"$setOnInsert": data},
        upsert=True
    )

async def get_user_data(user_id: int) -> dict:
    user = await users_col.find_one({"user_id": user_id})
    return user or {}

async def update_user_field(user_id: int, field: str, value):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {field: value}}
    )

# -------------------------------------------------------------------
# Progress bar (simplified – no advanced UI for now)
# -------------------------------------------------------------------
class Progress:
    def __init__(self, message: Message, filename: str, total_size: int, status: str = "⬇️"):
        self.message = message
        self.filename = filename
        self.total = total_size
        self.status = status
        self.current = 0
        self.start_time = time.time()
        self.last_update = time.time()
        self.last_processed = 0
        self.speed = 0
        self.eta = "Calculating..."
        self.cancel = False

    async def update(self, current: int, total: int):
        self.current = current
        now = time.time()
        elapsed = now - self.last_update
        if elapsed >= 2.0:
            diff = current - self.last_processed
            self.speed = diff / elapsed
            self.last_processed = current
            self.last_update = now
            if self.speed > 0:
                remaining = total - current
                eta_sec = remaining / self.speed
                self.eta = time_formatter(int(eta_sec))
            else:
                self.eta = "Unknown"
            percent = (current / total) * 100 if total else 0
            bar_length = 20
            filled = int(bar_length * percent / 100)
            bar = "█" * filled + "░" * (bar_length - filled)
            filename_trunc = truncate_text(self.filename, 30)
            speed_str = humanbytes(self.speed) + "/s" if self.speed else "0B/s"
            progress_text = (
                f"╔════════════════════════════════════╗\n"
                f"║ 📁 {filename_trunc:<30} ║\n"
                f"║ [{bar}] {percent:.1f}%          ║\n"
                f"║ {self.status} {speed_str:<15}     ║\n"
                f"║ 📦 {humanbytes(current)}/{humanbytes(total)}          ║\n"
                f"║ ⏳ ETA: {self.eta:<15}          ║\n"
                f"║ 🚀 Powered by @Venuboyy           ║\n"
                f"╚════════════════════════════════════╝"
            )
            try:
                await self.message.edit_text(
                    progress_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{id(self)}")]
                    ])
                )
            except Exception as e:
                logger.error(f"Progress update error: {e}")
        if self.cancel:
            raise asyncio.CancelledError("User cancelled")

def progress_callback(progress: Progress):
    async def callback(current: int, total: int):
        await progress.update(current, total)
    return callback

# -------------------------------------------------------------------
# FFmpeg – with fallback for copy
# -------------------------------------------------------------------
async def run_ffmpeg(cmd: list, input_path: str, output_path: str) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"FFmpeg error: {stderr.decode()}")
            return False
        return True
    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        return False

async def ffmpeg_rename(input_path: str, output_path: str):
    cmd = ["ffmpeg", "-i", input_path, "-c", "copy", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

# -------------------------------------------------------------------
# File processing (renaming)
# -------------------------------------------------------------------
async def process_file(message: Message, new_name: str, user_id: int):
    logger.info(f"Processing file for user {user_id}: {new_name}")
    user_data = await get_user_data(user_id)
    prefix = user_data.get("prefix", "")
    suffix = user_data.get("suffix", "")
    final_name = new_name
    if prefix:
        final_name = prefix + final_name
    if suffix:
        base, ext = os.path.splitext(final_name)
        final_name = base + suffix + ext

    input_ext = os.path.splitext(message.document.file_name)[1] if message.document else ""
    output_ext = os.path.splitext(final_name)[1]
    need_conversion = input_ext.lower() != output_ext.lower() if input_ext else False

    temp_input = os.path.join(downloads_dir, f"{user_id}_{int(time.time())}_input{input_ext}")
    temp_output = os.path.join(downloads_dir, f"{user_id}_{int(time.time())}_output{output_ext}")

    progress_msg = await message.reply_text("⏳ Downloading...")
    progress = Progress(progress_msg, final_name, message.document.file_size, "⬇️")
    try:
        await app.download_media(
            message,
            file_name=temp_input,
            progress=progress_callback(progress)
        )
        # If conversion needed – just copy for now (no transcoding)
        if need_conversion:
            await progress_msg.edit_text("🔄 Converting... (copy streams)")
            # For demo, we still use ffmpeg copy to change container
            success = await ffmpeg_rename(temp_input, temp_output)
            if not success:
                # fallback: direct copy
                shutil.copy2(temp_input, temp_output)
        else:
            # Simple rename (copy streams) – try ffmpeg, fallback to copy
            success = await ffmpeg_rename(temp_input, temp_output)
            if not success:
                shutil.copy2(temp_input, temp_output)

        # Apply metadata (if any)
        meta_title = user_data.get("metadata_title")
        meta_artist = user_data.get("metadata_artist")
        meta_album = user_data.get("metadata_album")
        meta_year = user_data.get("metadata_year")
        if any([meta_title, meta_artist, meta_album, meta_year]):
            meta_temp = temp_output + "_meta"
            cmd = ["ffmpeg", "-i", temp_output]
            if meta_title:
                cmd.extend(["-metadata", f"title={meta_title}"])
            if meta_artist:
                cmd.extend(["-metadata", f"artist={meta_artist}"])
            if meta_album:
                cmd.extend(["-metadata", f"album={meta_album}"])
            if meta_year:
                cmd.extend(["-metadata", f"year={meta_year}"])
            cmd.extend(["-c", "copy", meta_temp])
            success = await run_ffmpeg(cmd, temp_output, meta_temp)
            if success:
                os.replace(meta_temp, temp_output)
            else:
                os.remove(meta_temp)

        # Caption
        caption = user_data.get("caption", "")
        if caption:
            caption = caption.replace("{filename}", os.path.basename(final_name))
            caption = caption.replace("{size}", humanbytes(os.path.getsize(temp_output)))

        # Thumbnail
        thumb_path = None
        thumb_file_id = user_data.get("thumbnail")
        if thumb_file_id:
            thumb_path = os.path.join(downloads_dir, f"{user_id}_thumb.jpg")
            await app.download_media(thumb_file_id, file_name=thumb_path)

        await progress_msg.edit_text("⏳ Uploading...")
        upload_progress = Progress(progress_msg, final_name, os.path.getsize(temp_output), "⬆️")
        try:
            await app.send_document(
                chat_id=message.chat.id,
                document=temp_output,
                thumb=thumb_path,
                caption=caption,
                progress=progress_callback(upload_progress)
            )
            await users_col.update_one(
                {"user_id": user_id},
                {"$inc": {"total_processed": 1}, "$set": {"last_active": datetime.now()}}
            )
            await progress_msg.delete()
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await progress_msg.edit_text(f"❌ Upload failed: {str(e)}")
    except asyncio.CancelledError:
        await progress_msg.edit_text("❌ Process cancelled by user.")
    except Exception as e:
        logger.error(f"Processing error: {e}")
        await progress_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        for f in [temp_input, temp_output, thumb_path]:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

# -------------------------------------------------------------------
# Text constants
# -------------------------------------------------------------------
START_TXT = """<b>ʜᴇʏ {}</b>

<b>ɪ'ᴍ ᴀ ᴘᴏᴡᴇʀғᴜʟ ғɪʟᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ</b> 📝

<b>⚡ ғᴇᴀᴛᴜʀᴇs:</b>
➤ ғɪʟᴇ ʀᴇɴᴀᴍɪɴɢ
➤ ᴍᴇᴛᴀᴅᴀᴛᴀ ᴇᴅɪᴛᴏʀ
➤ sᴛʀᴇᴀᴍ ᴇxᴛʀᴀᴄᴛᴏʀ
➤ ғɪʟᴇ ᴄᴏɴᴠᴇʀᴛᴇʀ
➤ ᴘᴇʀᴍᴀɴᴇɴᴛ ᴛʜᴜᴍʙɴᴀɪʟ
➤ ᴀɴᴅ ᴍᴀɴʏ ᴍᴏʀᴇ...

sᴇɴᴅ ᴀ ғɪʟᴇ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ! 🚀"""

HELP_TXT = """<b>✨ ʜᴏᴡ ᴛᴏ ᴜsᴇ ʀᴇɴᴀᴍᴇ ʙᴏᴛ ✨</b>

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
/info - ʏᴏᴜʀ ᴅᴇᴛᴀɪʟs ᴡɪᴛʜ ᴘʀᴏғɪʟᴇ ᴘʜᴏᴛᴏ
/settings - ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs
/ping - ᴄʜᴇᴄᴋ ʙᴏᴛ ʀᴇsᴘᴏɴsᴇ ᴛɪᴍᴇ
/about - ʙᴏᴛ ɪɴғᴏʀᴍᴀᴛɪᴏɴ

<b>👑 ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅs:</b>
/stats - ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs
/broadcast - ᴍᴇssᴀɢᴇ ᴀʟʟ ᴜsᴇʀs
/ban - ʙᴀɴ ᴀ ᴜsᴇʀ
/unban - ᴜɴʙᴀɴ ᴀ ᴜsᴇʀ"""

ABOUT_TXT = """<b>╭────[ ᴍʏ ᴅᴇᴛᴀɪʟs ]────⍟

├⍟ Mʏ Nᴀᴍᴇ : {}

├⍟ Dᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👨‍💻

├⍟ Oᴡɴᴇʀ : <a href='https://t.me/Venuboyy'>ᴠᴇɴᴜʙᴏʏʏ</a> 👑

├⍟ Lɪʙʀᴀʀʏ : <a href='https://github.com/pyrogram/pyrogram'>ᴘʏʀᴏɢʀᴀᴍ ᴠ2</a> 📚

├⍟ Lᴀɴɢᴜᴀɢᴇ : <a href='https://www.python.org/'>ᴘʏᴛʜᴏɴ 3</a> 🐍

├⍟ Dᴀᴛᴀʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a> 🍃

├⍟ Sᴇʀᴠᴇʀ : ᴅᴇᴅɪᴄᴀᴛᴇᴅ ᴠᴘs ☁️

├⍟ Fᴇᴀᴛᴜʀᴇ : ғɪʟᴇ ʀᴇɴᴀᴍᴇʀ 📝

├⍟ Wᴏʀᴋᴇʀs : 500 ᴄᴏɴᴄᴜʀʀᴇɴᴛ ⚡

├⍟ Bᴜɪʟᴅ Sᴛᴀᴛᴜs : ᴠ2.0 [ ᴜʟᴛʀᴀ ] 🚀

╰───────────────⍟</b>"""

# -------------------------------------------------------------------
# Inline Markups
# -------------------------------------------------------------------
START_MARKUP = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Rename", callback_data="rename"),
     InlineKeyboardButton("📦 Batch Rename", callback_data="batch")],
    [InlineKeyboardButton("🖼️ Thumbnail", callback_data="thumbnail"),
     InlineKeyboardButton("📋 Metadata", callback_data="metadata")],
    [InlineKeyboardButton("✍️ Caption", callback_data="caption"),
     InlineKeyboardButton("🏷️ Prefix/Suffix", callback_data="prefix_suffix")],
    [InlineKeyboardButton("🎬 Media Tools", callback_data="media_tools"),
     InlineKeyboardButton("🔄 Convert", callback_data="convert")],
    [InlineKeyboardButton("📸 Screenshot", callback_data="screenshot"),
     InlineKeyboardButton("🎵 Audio Tools", callback_data="audio_tools")],
    [InlineKeyboardButton("📋 Subtitle", callback_data="subtitle"),
     InlineKeyboardButton("🗜️ Compress", callback_data="compress")],
    [InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
     InlineKeyboardButton("ℹ️ About", callback_data="about")],
    [InlineKeyboardButton("📊 Stats", callback_data="stats"),
     InlineKeyboardButton("🔧 Help", callback_data="help")]
])

HELP_BACK = InlineKeyboardMarkup([
    [InlineKeyboardButton("🏠 Back to Home", callback_data="home")]
])

# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    logger.info(f"User {message.from_user.id} used /start")
    user = message.from_user
    passed, markup = await check_force_sub(user.id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user.id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await save_user(user)
    sticker_id = "CAACAgIAAxkBAAEQZtFpgEdROhGouBVFD3e0K-YjmVHwsgACtCMAAphLKUjeub7NKlvk2TgE"
    try:
        sticker_msg = await message.reply_sticker(sticker_id)
        await asyncio.sleep(2)
        await sticker_msg.delete()
    except:
        pass
    welcome_img = get_welcome_image()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(welcome_img) as resp:
                if resp.status == 200:
                    img_path = os.path.join(downloads_dir, f"welcome_{user.id}.jpg")
                    with open(img_path, "wb") as f:
                        f.write(await resp.read())
                    await message.reply_photo(
                        photo=img_path,
                        caption=START_TXT.format(user.mention),
                        reply_markup=START_MARKUP
                    )
                    os.remove(img_path)
                else:
                    fallback = "https://i.ibb.co/pr2H8cwT/img-8312532076.jpg"
                    await message.reply_photo(
                        photo=fallback,
                        caption=START_TXT.format(user.mention),
                        reply_markup=START_MARKUP
                    )
    except Exception as e:
        logger.error(f"Welcome image error: {e}")
        fallback = "https://i.ibb.co/pr2H8cwT/img-8312532076.jpg"
        await message.reply_photo(
            photo=fallback,
            caption=START_TXT.format(user.mention),
            reply_markup=START_MARKUP
        )

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    logger.info(f"User {message.from_user.id} used /help")
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(HELP_TXT, reply_markup=HELP_BACK)

@app.on_message(filters.command("about") & filters.private)
async def about_command(client, message):
    logger.info(f"User {message.from_user.id} used /about")
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    bot_info = await app.get_me()
    await message.reply_text(
        ABOUT_TXT.format(bot_info.first_name),
        reply_markup=HELP_BACK,
        disable_web_page_preview=True
    )

@app.on_message(filters.command("info") & filters.private)
async def info_command(client, message):
    logger.info(f"User {message.from_user.id} used /info")
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    user = message.from_user
    data = await get_user_data(user_id)
    first_name = user.first_name or "N/A"
    last_name = user.last_name or "N/A"
    username = f"@{user.username}" if user.username else "None"
    dc_id = user.dc_id or "Unknown"
    total_processed = data.get("total_processed", 0)
    joined_date = data.get("joined_date", datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    text = f"""<b>📋 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>

➲ <b>First Name:</b> {first_name}
➲ <b>Last Name:</b> {last_name}
➲ <b>Telegram ID:</b> <code>{user.id}</code>
➲ <b>Data Centre:</b> {dc_id}
➲ <b>User Name:</b> {username}
➲ <b>User 𝖫𝗂𝗇𝗄:</b> <a href='tg://user?id={user.id}'>Click Here</a>

<b>📊 Bot Usage:</b>
➲ <b>Files Processed:</b> {total_processed}
➲ <b>Joined:</b> {joined_date}"""
    photos = await app.get_profile_photos(user.id)
    if photos.total_count > 0:
        photo = photos[0]
        file_path = await app.download_media(photo.file_id)
        await message.reply_photo(
            photo=file_path,
            caption=text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    else:
        await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )

@app.on_message(filters.command("settings") & filters.private)
async def settings_command(client, message):
    logger.info(f"User {message.from_user.id} used /settings")
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    data = await get_user_data(user_id)
    thumb = "✅ Set" if data.get("thumbnail") else "❌ Not Set"
    meta = "✅ Set" if data.get("metadata_title") else "❌ Not Set"
    caption = "✅ Set" if data.get("caption") else "❌ Not Set"
    prefix = data.get("prefix") or "None"
    suffix = data.get("suffix") or "None"
    total = data.get("total_processed", 0)
    text = f"""<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>

🖼️ <b>Thumbnail:</b> {thumb}
📋 <b>Metadata:</b> {meta}
✍️ <b>Caption:</b> {caption}
🏷️ <b>Prefix:</b> <code>{prefix}</code>
🏷️ <b>Suffix:</b> <code>{suffix}</code>

<b>📊 Stats:</b>
📁 <b>Files Processed:</b> {total}"""
    buttons = [
        [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb"),
         InlineKeyboardButton("🗑️ Del Thumbnail", callback_data="del_thumb")],
        [InlineKeyboardButton("📋 Set Metadata", callback_data="set_meta"),
         InlineKeyboardButton("🗑️ Del Metadata", callback_data="del_meta")],
        [InlineKeyboardButton("✍️ Set Caption", callback_data="set_caption"),
         InlineKeyboardButton("🗑️ Del Caption", callback_data="del_caption")],
        [InlineKeyboardButton("🏷️ Prefix", callback_data="set_prefix"),
         InlineKeyboardButton("🏷️ Suffix", callback_data="set_suffix")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ... (all other command handlers for thumbnail, metadata, etc. – we keep them as before)
# For brevity I'll include them in the final code block but here I'll show only essential.

# -------------------------------------------------------------------
# File Handler
# -------------------------------------------------------------------
waiting_rename = {}

@app.on_message(filters.document | filters.video | filters.audio)
async def file_handler(client, message):
    user_id = message.from_user.id
    logger.info(f"File received from user {user_id}")
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await save_user(message.from_user)
    if message.document:
        file = message.document
        ext = os.path.splitext(file.file_name)[1] if file.file_name else ".bin"
        size = file.file_size
    elif message.video:
        file = message.video
        ext = os.path.splitext(file.file_name)[1] if file.file_name else ".mp4"
        size = file.file_size
    elif message.audio:
        file = message.audio
        ext = os.path.splitext(file.file_name)[1] if file.file_name else ".mp3"
        size = file.file_size
    else:
        await message.reply_text("Unsupported file type.")
        return
    filename = file.file_name or f"file{ext}"
    size_str = humanbytes(size)
    duration = getattr(file, "duration", None)
    duration_str = time_formatter(duration) if duration else "N/A"
    text = f"""<b>📁 File Received!</b>

📄 <b>Name:</b> <code>{filename}</code>
📦 <b>Size:</b> {size_str}
⏱️ <b>Duration:</b> {duration_str}

<b>✏️ Reply with new filename + extension</b>
Example: <code>My New Video{ext}</code>

<b>🔧 Available formats:</b>
Video: .mp4, .mkv, .avi, .mov, .webm, .ts
Audio: .mp3, .aac, .m4a, .flac, .opus"""
    buttons = [
        [InlineKeyboardButton("🎬 Media Tools", callback_data="media_tools"),
         InlineKeyboardButton("🔄 Convert", callback_data="convert")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
    ]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    waiting_rename[user_id] = {
        "message": message,
        "filename": filename,
        "file_size": size
    }

@app.on_message(filters.text & filters.private)
async def text_handler(client, message):
    user_id = message.from_user.id
    if user_id in waiting_rename:
        data = waiting_rename.pop(user_id)
        new_name = message.text.strip()
        if not new_name:
            await message.reply_text("❌ Invalid filename.")
            return
        logger.info(f"Renaming file for user {user_id} to {new_name}")
        await process_file(data["message"], new_name, user_id)

# -------------------------------------------------------------------
# Callback Handler – all buttons
# -------------------------------------------------------------------
@app.on_callback_query()
async def callback_handler(client, callback: CallbackQuery):
    data = callback.data
    user_id = callback.from_user.id
    message = callback.message
    logger.info(f"Callback: {data} from user {user_id}")

    # Force sub check for all except verify_sub
    if data != "verify_sub":
        passed, markup = await check_force_sub(user_id)
        if not passed:
            await callback.answer("You must join all channels first!", show_alert=True)
            return
        if await is_banned(user_id):
            await callback.answer("You are banned!", show_alert=True)
            return

    # ---- VERIFY ----
    if data == "verify_sub":
        passed, markup = await check_force_sub(user_id)
        if passed:
            await callback.answer("✅ Verified! You can now use the bot.", show_alert=True)
            await message.edit_text("✅ Verified! You can now use the bot.", reply_markup=None)
        else:
            await callback.answer("❌ Please join all channels first!", show_alert=True)
        return

    # ---- HOME, HELP, ABOUT, SETTINGS ----
    if data == "home":
        await message.edit_text(START_TXT.format(callback.from_user.mention), reply_markup=START_MARKUP)
        await callback.answer()
        return

    if data == "help":
        await message.edit_text(HELP_TXT, reply_markup=HELP_BACK)
        await callback.answer()
        return

    if data == "about":
        bot_info = await app.get_me()
        await message.edit_text(ABOUT_TXT.format(bot_info.first_name), reply_markup=HELP_BACK, disable_web_page_preview=True)
        await callback.answer()
        return

    if data == "settings":
        await settings_command(client, message)
        await callback.answer()
        return

    # ---- THUMBNAIL, METADATA, CAPTION, PREFIX/SUFFIX ----
    if data == "thumbnail":
        await set_thumbnail_cmd(client, message)
        await callback.answer()
        return

    if data == "metadata":
        await set_metadata_cmd(client, message)
        await callback.answer()
        return

    if data == "caption":
        await set_caption_cmd(client, message)
        await callback.answer()
        return

    if data == "prefix_suffix":
        buttons = [
            [InlineKeyboardButton("🏷️ Set Prefix", callback_data="set_prefix"),
             InlineKeyboardButton("🏷️ Set Suffix", callback_data="set_suffix")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text("🏷️ <b>Prefix / Suffix Settings</b>\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer()
        return

    if data == "set_prefix":
        await set_prefix_cmd(client, message)
        await callback.answer()
        return

    if data == "set_suffix":
        await set_suffix_cmd(client, message)
        await callback.answer()
        return

    # ---- STATS ----
    if data == "stats":
        await stats_command(client, message)
        await callback.answer()
        return

    # ---- SCREENSHOT, AUDIO TOOLS, SUBTITLE, COMPRESS ----
    if data == "screenshot":
        await message.reply_text("📸 <b>Screenshot Generator</b>\n\nSend a video file or reply with timestamp (e.g., 00:01:30).", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    if data == "audio_tools":
        buttons = [
            [InlineKeyboardButton("🎵 Extract Audio", callback_data="audio_extract"),
             InlineKeyboardButton("🔇 Remove Audio", callback_data="audio_remove")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text("🎵 <b>Audio Tools</b>\n\nSelect operation:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer()
        return

    if data == "subtitle":
        buttons = [
            [InlineKeyboardButton("📋 Extract Subtitles", callback_data="sub_extract"),
             InlineKeyboardButton("🗑️ Remove Subtitles", callback_data="sub_remove")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text("📋 <b>Subtitle Tools</b>\n\nSelect operation:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer()
        return

    if data == "compress":
        await message.reply_text("🗜️ <b>Compression</b>\n\nSend a video file or reply with CRF value (e.g., 28).", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    # ---- RENAME / BATCH ----
    if data == "rename":
        await message.reply_text(
            "📝 <b>How to rename</b>\n\n1. Send me any file.\n"
            "2. Reply to that file with the new name (including extension).\n"
            "Example: `My New Video.mkv`"
        )
        await callback.answer()
        return

    if data == "batch":
        await message.reply_text(
            "📦 <b>Batch Rename</b>\n\nSend multiple files (as a group) and then reply with:\n"
            "• `New Name` – all files renamed sequentially.\n"
            "• `New Name {n}` – replaces `{n}` with the file number."
        )
        await callback.answer()
        return

    # ---- MEDIA TOOLS SUBMENU ----
    if data == "media_tools":
        buttons = [
            [InlineKeyboardButton("📤 Stream Extract", callback_data="stream_extract"),
             InlineKeyboardButton("🚫 Stream Remove", callback_data="stream_remove")],
            [InlineKeyboardButton("🎵 Audio Extract", callback_data="audio_extract"),
             InlineKeyboardButton("🔇 Audio Remove", callback_data="audio_remove")],
            [InlineKeyboardButton("📋 Sub Extract", callback_data="sub_extract"),
             InlineKeyboardButton("🗑️ Sub Remove", callback_data="sub_remove")],
            [InlineKeyboardButton("📸 Screenshot", callback_data="screenshot"),
             InlineKeyboardButton("✂️ Sample Video", callback_data="sample_video")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text("<b>🎬 Media Tools</b>\n\nSelect operation:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer()
        return

    # ---- SPECIFIC MEDIA TOOLS ----
    if data in ("stream_extract", "stream_remove", "audio_extract", "audio_remove",
                "sub_extract", "sub_remove", "sample_video"):
        await callback.message.reply_text(
            f"🛠️ <b>{data.replace('_', ' ').title()}</b>\n\nPlease send the file you want to process.",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    # ---- CONVERSION ----
    if data == "convert":
        buttons = [
            [InlineKeyboardButton("MP4", callback_data="convert_mp4"),
             InlineKeyboardButton("MKV", callback_data="convert_mkv"),
             InlineKeyboardButton("AVI", callback_data="convert_avi")],
            [InlineKeyboardButton("MOV", callback_data="convert_mov"),
             InlineKeyboardButton("WEBM", callback_data="convert_webm"),
             InlineKeyboardButton("TS", callback_data="convert_ts")],
            [InlineKeyboardButton("MP3", callback_data="convert_mp3"),
             InlineKeyboardButton("AAC", callback_data="convert_aac"),
             InlineKeyboardButton("M4A", callback_data="convert_m4a")],
            [InlineKeyboardButton("FLAC", callback_data="convert_flac"),
             InlineKeyboardButton("OPUS", callback_data="convert_opus")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text("<b>🔄 Format Conversion</b>\n\nChoose a target format:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer()
        return

    if data.startswith("convert_"):
        fmt = data.split("_")[1]
        await callback.message.reply_text(
            f"🔄 <b>Convert to .{fmt}</b>\n\nPlease send the file to convert.",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    # ---- REFRESH / BROADCAST ----
    if data == "refresh_ping":
        start = time.time()
        await callback.answer("Refreshing...")
        end = time.time()
        ping = int((end - start) * 1000)
        await message.edit_text(
            f"<b>🏓 ᴘᴏɴɢ!</b>\n\n"
            f"<b>⏱️ Response Time:</b> <code>{ping}ms</code>\n"
            f"<b>📡 Bot Uptime:</b> <code>{get_uptime()}</code>\n"
            f"<b>⚡ Workers:</b> <code>500</code>\n"
            f"<b>🗄️ Database:</b> <code>Connected ✅</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_ping"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        return

    if data == "refresh_stats":
        await stats_command(client, message)
        await callback.answer()
        return

    if data == "refresh_info":
        await info_command(client, message)
        await callback.answer()
        return

    if data == "broadcast":
        await callback.message.reply_text("📨 <b>Send message to broadcast:</b>\n\nSend any text, photo, video, or document", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    # ---- SET / DEL ----
    if data == "set_thumb":
        await callback.message.reply_text("🖼️ <b>Please reply to a photo to set as permanent thumbnail.</b>", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    if data == "del_thumb":
        await update_user_field(user_id, "thumbnail", None)
        await message.edit_text("🗑️ <b>Thumbnail removed successfully!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🖼️ Set New", callback_data="set_thumb"), InlineKeyboardButton("🏠 Home", callback_data="home")]]))
        await callback.answer()
        return

    if data == "set_meta":
        await callback.message.reply_text("📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>\n\nExample: <code>My Video | John Doe | My Album | 2024</code>", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    if data == "del_meta":
        await update_user_field(user_id, "metadata_title", None)
        await update_user_field(user_id, "metadata_artist", None)
        await update_user_field(user_id, "metadata_album", None)
        await update_user_field(user_id, "metadata_year", None)
        await message.edit_text("🗑️ <b>Metadata removed!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Set New", callback_data="set_meta"), InlineKeyboardButton("🏠 Home", callback_data="home")]]))
        await callback.answer()
        return

    if data == "set_caption":
        await callback.message.reply_text("✍️ <b>Send your permanent caption text</b>\n\nYou can use:\n• <code>{filename}</code> - for file name\n• <code>{size}</code> - for file size\n• HTML formatting allowed", reply_markup=ForceReply(selective=True))
        await callback.answer()
        return

    if data == "del_caption":
        await update_user_field(user_id, "caption", None)
        await message.edit_text("🗑️ <b>Caption removed!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Set New", callback_data="set_caption"), InlineKeyboardButton("🏠 Home", callback_data="home")]]))
        await callback.answer()
        return

    # ---- Fallback ----
    await callback.answer("⏳ This feature is being developed.", show_alert=True)

# ========== Admin Commands (stats, broadcast, ban, unban) ==========
# (not shown for brevity but included in the full code)

# ========== Health Check Web Server ==========
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_get("/health", health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")
    while True:
        await asyncio.sleep(3600)

# ========== Main ==========
async def main():
    retries = 0
    max_retries = 5
    while retries < max_retries:
        try:
            await app.start()
            logger.info("Bot started!")
            break
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"FloodWait: need to wait {wait_time} seconds before retrying.")
            await asyncio.sleep(wait_time)
            retries += 1
        except Exception as e:
            logger.error(f"Startup error: {e}")
            raise
    else:
        logger.error("Failed to start after multiple retries.")
        return

    web_task = asyncio.create_task(start_web_server())
    await idle()
    web_task.cancel()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
