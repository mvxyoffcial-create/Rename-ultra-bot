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
import ffmpeg
import psutil
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery,
    Message, User, InputMediaPhoto, ForceReply
)
from pyrogram.errors import FloodWait, UserNotParticipant, RPCError
from pyrogram.enums import ParseMode

# Configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URL,
    ADMIN_IDS, FORCE_SUB_CHANNELS, PICS_URL
)

# Port for health check – read from env directly
PORT = int(os.getenv("PORT", 8000))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global vars
START_TIME = time.time()
executor = concurrent.futures.ThreadPoolExecutor(max_workers=500)
queue = asyncio.Queue(maxsize=100)
semaphore = asyncio.Semaphore(10)
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
# Helper Functions
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
    if len(text) > length:
        return text[:length-3] + "..."
    return text

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
# Database Helpers
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
# Progress Bar (Premium Style)
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
        if elapsed >= 2.0:  # update every 2 seconds
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
                f"║                                    ║\n"
                f"║ [{bar}] {percent:.1f}%          ║\n"
                f"║                                    ║\n"
                f"║ {self.status} {speed_str:<15}     ║\n"
                f"║ 📦 {humanbytes(current)}/{humanbytes(total)}          ║\n"
                f"║ ⏳ ETA: {self.eta:<15}          ║\n"
                f"║                                    ║\n"
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

    async def finish(self, success: bool = True):
        if success:
            await self.message.edit_text("✅ Process completed!")
        else:
            await self.message.edit_text("❌ Process failed!")

def progress_callback(progress: Progress):
    async def callback(current: int, total: int):
        await progress.update(current, total)
    return callback

# -------------------------------------------------------------------
# FFmpeg Commands
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

async def ffmpeg_extract_video(input_path: str, output_path: str):
    cmd = ["ffmpeg", "-i", input_path, "-map", "0:v", "-c", "copy", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_extract_audio(input_path: str, output_path: str):
    cmd = ["ffmpeg", "-i", input_path, "-vn", "-acodec", "libmp3lame", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_remove_audio(input_path: str, output_path: str):
    cmd = ["ffmpeg", "-i", input_path, "-an", "-c", "copy", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_convert_video(input_path: str, output_path: str, codec: str = "libx264"):
    cmd = ["ffmpeg", "-i", input_path, "-c:v", codec, "-preset", "fast", "-crf", "23", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_convert_audio(input_path: str, output_path: str, codec: str = "libmp3lame"):
    cmd = ["ffmpeg", "-i", input_path, "-vn", "-acodec", codec, output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_screenshot(input_path: str, output_path: str, timestamp: str = "00:01:00"):
    cmd = ["ffmpeg", "-i", input_path, "-ss", timestamp, "-vframes", "1", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_sample_video(input_path: str, output_path: str, duration: int = 60):
    cmd = ["ffmpeg", "-i", input_path, "-ss", "0", "-t", str(duration), "-c", "copy", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

async def ffmpeg_compress(input_path: str, output_path: str, crf: int = 28):
    cmd = ["ffmpeg", "-i", input_path, "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-c:a", "aac", "-b:a", "128k", output_path]
    return await run_ffmpeg(cmd, input_path, output_path)

# -------------------------------------------------------------------
# File Processing
# -------------------------------------------------------------------

async def process_file(message: Message, new_name: str, user_id: int):
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
        if need_conversion:
            await progress_msg.edit_text("🔄 Converting...")
            if output_ext in ['.mp3', '.aac', '.m4a', '.flac', '.opus']:
                codec_map = {'.mp3': 'libmp3lame', '.aac': 'aac', '.m4a': 'aac', '.flac': 'flac', '.opus': 'libopus'}
                codec = codec_map.get(output_ext, 'libmp3lame')
                success = await ffmpeg_convert_audio(temp_input, temp_output, codec)
            else:
                success = await ffmpeg_convert_video(temp_input, temp_output)
            if not success:
                await progress_msg.edit_text("❌ Conversion failed!")
                return
        else:
            success = await ffmpeg_rename(temp_input, temp_output)
            if not success:
                shutil.copy2(temp_input, temp_output)

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

        caption = user_data.get("caption", "")
        if caption:
            caption = caption.replace("{filename}", os.path.basename(final_name))
            caption = caption.replace("{size}", humanbytes(os.path.getsize(temp_output)))

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
# Text Constants
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
# Command Handlers
# -------------------------------------------------------------------

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    user = message.from_user
    passed, markup = await check_force_sub(user.id)
    if not passed:
        await message.reply_text(
            "⚠️ You must join our channels to use this bot!",
            reply_markup=markup
        )
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

@app.on_message(filters.command("thumbnail") & filters.private)
async def set_thumbnail_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    if message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo
        file_id = photo.file_id
        await update_user_field(user_id, "thumbnail", file_id)
        await message.reply_photo(
            photo=file_id,
            caption="✅ <b>Permanent thumbnail set successfully!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
    else:
        await message.reply_text(
            "🖼️ <b>Please reply to a photo to set as permanent thumbnail.</b>",
            reply_markup=ForceReply(selective=True)
        )

@app.on_message(filters.command("delthumbnail") & filters.private)
async def del_thumbnail_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await update_user_field(user_id, "thumbnail", None)
    await message.reply_text(
        "🗑️ <b>Thumbnail removed successfully!</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Set New", callback_data="set_thumb"),
             InlineKeyboardButton("🏠 Home", callback_data="home")]
        ])
    )

@app.on_message(filters.command("metadata") & filters.private)
async def set_metadata_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(
        "📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>\n\nExample: <code>My Video | John Doe | My Album | 2024</code>",
        reply_markup=ForceReply(selective=True)
    )

@app.on_message(filters.command("delmetadata") & filters.private)
async def del_metadata_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await update_user_field(user_id, "metadata_title", None)
    await update_user_field(user_id, "metadata_artist", None)
    await update_user_field(user_id, "metadata_album", None)
    await update_user_field(user_id, "metadata_year", None)
    await message.reply_text(
        "🗑️ <b>Metadata removed!</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Set New", callback_data="set_meta"),
             InlineKeyboardButton("🏠 Home", callback_data="home")]
        ])
    )

@app.on_message(filters.command("caption") & filters.private)
async def set_caption_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(
        "✍️ <b>Send your permanent caption text</b>\n\nYou can use:\n• <code>{filename}</code> - for file name\n• <code>{size}</code> - for file size\n• HTML formatting allowed",
        reply_markup=ForceReply(selective=True)
    )

@app.on_message(filters.command("delcaption") & filters.private)
async def del_caption_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await update_user_field(user_id, "caption", None)
    await message.reply_text(
        "🗑️ <b>Caption removed!</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Set New", callback_data="set_caption"),
             InlineKeyboardButton("🏠 Home", callback_data="home")]
        ])
    )

@app.on_message(filters.command("prefix") & filters.private)
async def set_prefix_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(
        "🏷️ <b>Send prefix text to add BEFORE filename</b>\n\nExample: <code>[TeamName]_</code>\nResult: <code>[TeamName]_filename.mkv</code>",
        reply_markup=ForceReply(selective=True)
    )

@app.on_message(filters.command("suffix") & filters.private)
async def set_suffix_cmd(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(
        "🏷️ <b>Send suffix text to add AFTER filename</b>\n\nExample: <code>@TeamName</code>\nResult: <code>filename@TeamName.mkv</code>",
        reply_markup=ForceReply(selective=True)
    )

@app.on_message(filters.command("ping") & filters.private)
async def ping_command(client, message):
    user_id = message.from_user.id
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    start = time.time()
    msg = await message.reply_text("🏓 Pinging...")
    end = time.time()
    ping = int((end - start) * 1000)
    await msg.edit_text(
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

# -------------------------------------------------------------------
# Admin Commands
# -------------------------------------------------------------------

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client, message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply_text("⛔ Access denied.")
        return
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    total_users = await users_col.count_documents({})
    active_today = await users_col.count_documents({"last_active": {"$gte": datetime.now().replace(hour=0, minute=0, second=0)}})
    banned_count = await users_col.count_documents({"banned": True})
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = get_uptime()
    queue_size = queue.qsize()
    text = f"""<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>

<b>👥 Users:</b>
• Total: {total_users}
• Active Today: {active_today}
• Banned: {banned_count}

<b>💻 System:</b>
• CPU: {cpu}%
• RAM: {humanbytes(mem.used)}/{humanbytes(mem.total)} ({mem.percent}%)
• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)

<b>⚡ Bot:</b>
• Uptime: {uptime}
• Workers: 500
• Queue: {queue_size} pending

<b>🗄️ Database:</b>
• Status: Connected ✅
• Database: MongoDB"""
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats"),
             InlineKeyboardButton("📨 Broadcast", callback_data="broadcast"),
             InlineKeyboardButton("🏠 Home", callback_data="home")]
        ])
    )

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply_text("⛔ Access denied.")
        return
    passed, markup = await check_force_sub(user_id)
    if not passed:
        await message.reply_text("⚠️ You must join our channels to use this bot!", reply_markup=markup)
        return
    if await is_banned(user_id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return
    await message.reply_text(
        "📨 <b>Send message to broadcast:</b>\n\nSend any text, photo, video, or document",
        reply_markup=ForceReply(selective=True)
    )

@app.on_message(filters.command("ban") & filters.private)
async def ban_command(client, message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply_text("⛔ Access denied.")
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /ban <user_id>")
        return
    target_id = int(message.command[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"banned": True}}, upsert=True)
    await message.reply_text(f"🚫 <b>User {target_id} banned!</b>")
    try:
        await app.send_message(target_id, "⚠️ <b>You have been banned from using this bot!</b>")
    except:
        pass

@app.on_message(filters.command("unban") & filters.private)
async def unban_command(client, message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply_text("⛔ Access denied.")
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /unban <user_id>")
        return
    target_id = int(message.command[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"banned": False}}, upsert=True)
    await message.reply_text(f"✅ <b>User {target_id} unbanned!</b>")
    try:
        await app.send_message(target_id, "✅ <b>You have been unbanned! You can use the bot now.</b>")
    except:
        pass

# -------------------------------------------------------------------
# File Rename Handler
# -------------------------------------------------------------------

waiting_rename = {}

@app.on_message(filters.document | filters.video | filters.audio)
async def file_handler(client, message):
    user_id = message.from_user.id
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
        await process_file(data["message"], new_name, user_id)

# -------------------------------------------------------------------
# Callback Query Handler
# -------------------------------------------------------------------

@app.on_callback_query()
async def callback_handler(client, callback: CallbackQuery):
    data = callback.data
    user_id = callback.from_user.id
    message = callback.message

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
        await message.edit_text(
            START_TXT.format(callback.from_user.mention),
            reply_markup=START_MARKUP
        )
        await callback.answer()
        return

    if data == "help":
        await message.edit_text(HELP_TXT, reply_markup=HELP_BACK)
        await callback.answer()
        return

    if data == "about":
        bot_info = await app.get_me()
        await message.edit_text(
            ABOUT_TXT.format(bot_info.first_name),
            reply_markup=HELP_BACK,
            disable_web_page_preview=True
        )
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
        await message.edit_text(
            "🏷️ <b>Prefix / Suffix Settings</b>\n\nChoose an option:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
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
        await message.reply_text(
            "📸 <b>Screenshot Generator</b>\n\nSend a video file or reply to a video with the timestamp (e.g., 00:01:30).",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    if data == "audio_tools":
        buttons = [
            [InlineKeyboardButton("🎵 Extract Audio", callback_data="audio_extract"),
             InlineKeyboardButton("🔇 Remove Audio", callback_data="audio_remove")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text(
            "🎵 <b>Audio Tools</b>\n\nSelect operation:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback.answer()
        return

    if data == "subtitle":
        buttons = [
            [InlineKeyboardButton("📋 Extract Subtitles", callback_data="sub_extract"),
             InlineKeyboardButton("🗑️ Remove Subtitles", callback_data="sub_remove")],
            [InlineKeyboardButton("🏠 Back", callback_data="home")]
        ]
        await message.edit_text(
            "📋 <b>Subtitle Tools</b>\n\nSelect operation:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback.answer()
        return

    if data == "compress":
        await message.reply_text(
            "🗜️ <b>Compression</b>\n\nSend a video file or reply with the target CRF value (e.g., 28).",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    # ---- RENAME / BATCH RENAME ----
    if data == "rename":
        await message.reply_text(
            "📝 <b>How to rename</b>\n\n1. Send me any file (video, audio, document).\n"
            "2. Reply to that file with the new name including extension.\n"
            "Example: `My New Video.mkv`"
        )
        await callback.answer()
        return

    if data == "batch":
        await message.reply_text(
            "📦 <b>Batch Rename</b>\n\nSend multiple files (as a group) and then reply with:\n"
            "• `New Name` – all files will be renamed sequentially.\n"
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
        await message.edit_text(
            "<b>🎬 Media Tools</b>\n\nSelect operation:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback.answer()
        return

    # ---- SPECIFIC MEDIA TOOLS (sub‑buttons) ----
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
        await message.edit_text(
            "<b>🔄 Format Conversion</b>\n\nChoose a target format:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
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
        await callback.message.reply_text(
            "📨 <b>Send message to broadcast:</b>\n\nSend any text, photo, video, or document",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    # ---- SET / DEL THUMB, META, CAPTION (already have handlers) ----
    if data == "set_thumb":
        await callback.message.reply_text(
            "🖼️ <b>Please reply to a photo to set as permanent thumbnail.</b>",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    if data == "del_thumb":
        await update_user_field(user_id, "thumbnail", None)
        await message.edit_text(
            "🗑️ <b>Thumbnail removed successfully!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖼️ Set New", callback_data="set_thumb"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        await callback.answer()
        return

    if data == "set_meta":
        await callback.message.reply_text(
            "📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>\n\nExample: <code>My Video | John Doe | My Album | 2024</code>",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    if data == "del_meta":
        await update_user_field(user_id, "metadata_title", None)
        await update_user_field(user_id, "metadata_artist", None)
        await update_user_field(user_id, "metadata_album", None)
        await update_user_field(user_id, "metadata_year", None)
        await message.edit_text(
            "🗑️ <b>Metadata removed!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Set New", callback_data="set_meta"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        await callback.answer()
        return

    if data == "set_caption":
        await callback.message.reply_text(
            "✍️ <b>Send your permanent caption text</b>\n\nYou can use:\n• <code>{filename}</code> - for file name\n• <code>{size}</code> - for file size\n• HTML formatting allowed",
            reply_markup=ForceReply(selective=True)
        )
        await callback.answer()
        return

    if data == "del_caption":
        await update_user_field(user_id, "caption", None)
        await message.edit_text(
            "🗑️ <b>Caption removed!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Set New", callback_data="set_caption"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        await callback.answer()
        return

    # ---- FALLBACK ----
    await callback.answer("⏳ This feature is being developed.", show_alert=True)

# -------------------------------------------------------------------
# Health Check Web Server
# -------------------------------------------------------------------

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

# -------------------------------------------------------------------
# Main – with retry logic and correct idle usage
# -------------------------------------------------------------------

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

    # Start the health‑check web server in the background
    web_task = asyncio.create_task(start_web_server())

    # Idle the bot – blocks until stopped
    await idle()

    # Cleanup
    web_task.cancel()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
