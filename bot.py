"""
Telegram File Rename Bot
Pyrogram v2 + MongoDB (motor) + ffmpeg

Single-file bot logic (imports config.py, database.py, progress.py,
ffmpeg_utils.py from the same package).

Run:  python bot.py
"""

import asyncio
import os
import time
import uuid
import logging
import datetime

import psutil
from aiohttp import web
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
)
from pyrogram.errors import FloodWait

import config
import ffmpeg_utils
from database import db
from progress import ProgressTracker, humanbytes, time_formatter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("rename_bot")

os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)


async def safe_db_call(coro, default=None, label="db call"):
    """
    Wrap any DB-touching coroutine so that if MongoDB is slow/unreachable
    (e.g. Atlas IP allowlist doesn't include the host's egress IP), the bot
    fails open instead of hanging silently forever on every message.
    """
    try:
        return await coro
    except Exception as e:
        log.warning(f"{label} failed, DB may be unreachable: {e}")
        return default


EMPTY_USER = {
    "thumbnail": None, "metadata_title": None, "metadata_artist": None,
    "metadata_album": None, "metadata_year": None, "caption": None,
    "prefix": None, "suffix": None, "banned": False, "total_processed": 0,
    "joined_date": None,
}

app = Client(
    "rename_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=config.PYROGRAM_WORKERS,
)

# --------------------------------------------------------------------- #
# In-memory state
# --------------------------------------------------------------------- #
# pending[user_id] = {"action": "await_rename", "message": Message, ...}
pending = {}
# active cancel events, keyed by stop_token
cancel_events = {}
# simple in-process queue depth counter (for /stats display)
queue_depth = 0

START_TIME = time.time()

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "ts", "flv", "wmv", "m4v"}
AUDIO_EXTS = {"mp3", "aac", "m4a", "flac", "opus", "wav", "ogg"}


# ======================================================================= #
# Text templates
# ======================================================================= #

START_TXT = """<b>ʜᴇʏ {}!</b>

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
• sᴇɴᴅ ᴍᴜʟᴛɪᴘʟᴇ ғɪʟᴇs ᴏɴᴇ ᴀғᴛᴇʀ ᴀɴᴏᴛʜᴇʀ
• ᴜsᴇ /batch ᴛᴏ sᴛᴀʀᴛ, ᴛʜᴇɴ ɢɪᴠᴇ ᴀ ɴᴀᴍᴇ ᴘᴀᴛᴛᴇʀɴ

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
• sᴛʀᴇᴀᴍ ᴇxᴛʀᴀᴄᴛᴏʀ / ʀᴇᴍᴏᴠᴇʀ
• ᴀᴜᴅɪᴏ ᴇxᴛʀᴀᴄᴛᴏʀ / ʀᴇᴍᴏᴠᴇʀ
• sᴜʙᴛɪᴛʟᴇ ᴇxᴛʀᴀᴄᴛᴏʀ / ʀᴇᴍᴏᴠᴇʀ
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

├⍟ Dᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/{dev_handle}'>{dev_name}</a> 👨‍💻

├⍟ Oᴡɴᴇʀ : <a href='https://t.me/{owner_handle}'>{owner_name}</a> 👑

├⍟ Lɪʙʀᴀʀʏ : <a href='https://github.com/pyrogram/pyrogram'>ᴘʏʀᴏɢʀᴀᴍ ᴠ2</a> 📚

├⍟ Lᴀɴɢᴜᴀɢᴇ : <a href='https://www.python.org/'>ᴘʏᴛʜᴏɴ 3</a> 🐍

├⍟ Dᴀᴛᴀʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a> 🍃

├⍟ Sᴇʀᴠᴇʀ : ᴅᴇᴅɪᴄᴀᴛᴇᴅ ᴠᴘs ☁️

├⍟ Fᴇᴀᴛᴜʀᴇ : ғɪʟᴇ ʀᴇɴᴀᴍᴇʀ 📝

├⍟ Bᴜɪʟᴅ Sᴛᴀᴛᴜs : ᴠ2.0 [ ᴜʟᴛʀᴀ ] 🚀

╰───────────────⍟</b>"""


# ======================================================================= #
# Keyboards
# ======================================================================= #

def home_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Rename", callback_data="how_rename"),
             InlineKeyboardButton("📦 Batch Rename", callback_data="how_batch")],
            [InlineKeyboardButton("🖼️ Thumbnail", callback_data="menu_thumb"),
             InlineKeyboardButton("📋 Metadata", callback_data="menu_meta")],
            [InlineKeyboardButton("✍️ Caption", callback_data="menu_caption"),
             InlineKeyboardButton("🏷️ Prefix/Suffix", callback_data="menu_prefsuf")],
            [InlineKeyboardButton("🎬 Media Tools", callback_data="how_media"),
             InlineKeyboardButton("⚙️ Settings", callback_data="open_settings")],
            [InlineKeyboardButton("📊 Stats", callback_data="open_stats"),
             InlineKeyboardButton("ℹ️ About", callback_data="open_about")],
            [InlineKeyboardButton("🔧 Help", callback_data="open_help")],
        ]
    )


def back_home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Home", callback_data="open_home")]])


def settings_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="menu_thumb"),
             InlineKeyboardButton("🗑️ Del Thumbnail", callback_data="del_thumb")],
            [InlineKeyboardButton("📋 Set Metadata", callback_data="menu_meta"),
             InlineKeyboardButton("🗑️ Del Metadata", callback_data="del_meta")],
            [InlineKeyboardButton("✍️ Set Caption", callback_data="menu_caption"),
             InlineKeyboardButton("🗑️ Del Caption", callback_data="del_caption")],
            [InlineKeyboardButton("🏷️ Prefix", callback_data="menu_prefix"),
             InlineKeyboardButton("🏷️ Suffix", callback_data="menu_suffix")],
            [InlineKeyboardButton("🏠 Home", callback_data="open_home")],
        ]
    )


def media_tools_keyboard(file_key: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 Stream Extract", callback_data=f"mt_streamex_{file_key}"),
             InlineKeyboardButton("🚫 Stream Remove", callback_data=f"mt_streamrm_{file_key}")],
            [InlineKeyboardButton("🎵 Audio Extract", callback_data=f"mt_audioex_{file_key}"),
             InlineKeyboardButton("🔇 Audio Remove", callback_data=f"mt_audiorm_{file_key}")],
            [InlineKeyboardButton("📋 Sub Extract", callback_data=f"mt_subex_{file_key}"),
             InlineKeyboardButton("🗑️ Sub Remove", callback_data=f"mt_subrm_{file_key}")],
            [InlineKeyboardButton("📸 Screenshot", callback_data=f"mt_ss_{file_key}"),
             InlineKeyboardButton("✂️ Sample Video", callback_data=f"mt_sample_{file_key}")],
            [InlineKeyboardButton("🗜️ Compress", callback_data=f"mt_compress_{file_key}")],
            [InlineKeyboardButton("🏠 Back", callback_data="open_home")],
        ]
    )


def convert_keyboard(file_key: str):
    buttons = []
    row = []
    for i, fmt in enumerate(config.SUPPORTED_VIDEO_FORMATS, 1):
        row.append(InlineKeyboardButton(fmt.upper(), callback_data=f"cv_{fmt}_{file_key}"))
        if i % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    row = []
    for i, fmt in enumerate(config.SUPPORTED_AUDIO_FORMATS, 1):
        row.append(InlineKeyboardButton(fmt.upper(), callback_data=f"ca_{fmt}_{file_key}"))
        if i % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🏠 Back", callback_data="open_home")])
    return InlineKeyboardMarkup(buttons)


def cancel_keyboard(stop_token: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{stop_token}")]])


# ======================================================================= #
# File registry (short keys for callback_data, which has a 64-byte limit)
# ======================================================================= #
file_registry = {}


def register_file(message: Message) -> str:
    key = uuid.uuid4().hex[:10]
    file_registry[key] = message
    return key


# ======================================================================= #
# Helpers
# ======================================================================= #

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def get_media(message: Message):
    return message.document or message.video or message.audio or message.animation


def media_filename(message: Message) -> str:
    media = get_media(message)
    name = getattr(media, "file_name", None)
    if name:
        return name
    if message.video:
        return f"video_{message.video.file_unique_id}.mp4"
    if message.audio:
        return f"audio_{message.audio.file_unique_id}.mp3"
    return f"file_{media.file_unique_id}"


async def apply_user_customizations(path: str, user: dict) -> str:
    """Apply saved metadata to a downloaded file, if the user has any set."""
    if any([user.get("metadata_title"), user.get("metadata_artist"), user.get("metadata_album"), user.get("metadata_year")]):
        try:
            new_path = await ffmpeg_utils.embed_metadata(
                path,
                title=user.get("metadata_title"),
                artist=user.get("metadata_artist"),
                album=user.get("metadata_album"),
                year=user.get("metadata_year"),
            )
            os.remove(path)
            return new_path
        except ffmpeg_utils.FFmpegError as e:
            log.warning(f"Metadata embed failed: {e}")
    return path


def build_final_name(base_name: str, user: dict) -> str:
    name, ext = os.path.splitext(base_name)
    prefix = user.get("prefix") or ""
    suffix = user.get("suffix") or ""
    return f"{prefix}{name}{suffix}{ext}"


def format_about(bot_name: str) -> str:
    dev_handle = config.DEVELOPER.lstrip("@")
    owner_handle = getattr(config, "OWNER", config.DEVELOPER).lstrip("@")
    return ABOUT_TXT.format(
        bot_name,
        dev_handle=dev_handle,
        dev_name=dev_handle,
        owner_handle=owner_handle,
        owner_name=owner_handle,
    )


def build_caption(filename: str, size: int, user: dict) -> str:
    template = user.get("caption")
    if template:
        return template.replace("{filename}", filename).replace("{size}", humanbytes(size))
    return f"<code>{filename}</code>\n📦 {humanbytes(size)}"


# ======================================================================= #
# /start /help /about /info /settings /ping
# ======================================================================= #

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    try:
        user = message.from_user
        if await safe_db_call(db.is_banned(user.id), default=False, label="is_banned"):
            await message.reply_text("🚫 <b>You are banned from using this bot.</b>")
            return

        await safe_db_call(db.add_user_if_new(user.id, user.first_name, user.last_name, user.username), default=False, label="add_user_if_new")

        text = START_TXT.format(user.mention)
        await message.reply_text(text, reply_markup=home_keyboard())
    except Exception:
        log.exception("start_cmd failed")
        try:
            await message.reply_text("⚠️ Something went wrong starting up. Please try again in a moment.")
        except Exception:
            pass


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client: Client, message: Message):
    if await safe_db_call(db.is_banned(message.from_user.id), default=False, label="is_banned"):
        return
    await message.reply_text(HELP_TXT, reply_markup=back_home_kb(), disable_web_page_preview=True)


@app.on_message(filters.command("about") & filters.private)
async def about_cmd(client: Client, message: Message):
    if await safe_db_call(db.is_banned(message.from_user.id), default=False, label="is_banned"):
        return
    me = await client.get_me()
    await message.reply_text(
        format_about(me.first_name), reply_markup=back_home_kb(), disable_web_page_preview=True
    )


@app.on_message(filters.command("info") & filters.private)
async def info_cmd(client: Client, message: Message):
    user = message.from_user
    if await safe_db_call(db.is_banned(user.id), default=False, label="is_banned"):
        return
    udoc = await safe_db_call(db.get_user(user.id), default=dict(EMPTY_USER), label="get_user")

    try:
        dc_id = user.dc_id or "N/A"
    except Exception:
        dc_id = "N/A"

    caption = (
        "<b>📋 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>\n\n"
        f"➲ <b>First Name:</b> {user.first_name or ''}\n"
        f"➲ <b>Last Name:</b> {user.last_name or ''}\n"
        f"➲ <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"➲ <b>Data Centre:</b> {dc_id}\n"
        f"➲ <b>User Name:</b> @{user.username or 'None'}\n"
        f"➲ <b>User Link:</b> <a href='tg://user?id={user.id}'>Click Here</a>\n\n"
        "<b>📊 Bot Usage:</b>\n"
        f"➲ <b>Files Processed:</b> {udoc.get('total_processed', 0)}\n"
        f"➲ <b>Joined:</b> {udoc.get('joined_date').strftime('%d %b %Y') if udoc.get('joined_date') else 'N/A'}"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info"),
          InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )

    photos = []
    try:
        async for p in client.get_chat_photos(user.id, limit=1):
            photos.append(p)
    except Exception:
        pass

    if photos:
        await message.reply_photo(photos[0].file_id, caption=caption, reply_markup=kb)
    else:
        await message.reply_text(caption, reply_markup=kb, disable_web_page_preview=True)


@app.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await safe_db_call(db.is_banned(user_id), default=False, label="is_banned"):
        return
    u = await safe_db_call(db.get_user(user_id), default=dict(EMPTY_USER), label="get_user")
    text = (
        "<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>\n\n"
        f"🖼️ <b>Thumbnail:</b> {'✅ Set' if u.get('thumbnail') else '❌ Not Set'}\n"
        f"📋 <b>Metadata:</b> {'✅ Set' if u.get('metadata_title') else '❌ Not Set'}\n"
        f"✍️ <b>Caption:</b> {'✅ Set' if u.get('caption') else '❌ Not Set'}\n"
        f"🏷️ <b>Prefix:</b> {u.get('prefix') or 'None'}\n"
        f"🏷️ <b>Suffix:</b> {u.get('suffix') or 'None'}\n\n"
        "<b>📊 Stats:</b>\n"
        f"📁 <b>Files Processed:</b> {u.get('total_processed', 0)}"
    )
    await message.reply_text(text, reply_markup=settings_keyboard())


@app.on_message(filters.command("ping") & filters.private)
async def ping_cmd(client: Client, message: Message):
    start = time.time()
    msg = await message.reply_text("🏓 Pinging...")
    ping_time = (time.time() - start) * 1000
    uptime = time_formatter(time.time() - START_TIME)
    text = (
        "<b>🏓 ᴘᴏɴɢ!</b>\n\n"
        f"<b>⏱️ Response Time:</b> <code>{ping_time:.2f}ms</code>\n"
        f"<b>📡 Bot Uptime:</b> <code>{uptime}</code>\n"
        f"<b>⚡ Workers:</b> <code>{config.PYROGRAM_WORKERS}</code>\n"
        f"<b>🗄️ Database:</b> <code>Connected ✅</code>"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_ping"),
          InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )
    await msg.edit_text(text, reply_markup=kb)


# ======================================================================= #
# Thumbnail
# ======================================================================= #

@app.on_message(filters.command("thumbnail") & filters.private)
async def thumbnail_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await safe_db_call(db.is_banned(user_id), default=False, label="is_banned"):
        return

    if message.reply_to_message and message.reply_to_message.photo:
        file_id = message.reply_to_message.photo.file_id
        await db.set_thumbnail(user_id, file_id)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_photo(
            file_id, caption="✅ <b>Permanent thumbnail set successfully!</b>", reply_markup=kb
        )
    else:
        pending[user_id] = {"action": "await_thumbnail"}
        await message.reply_text("🖼️ <b>Please send a photo to set as your permanent thumbnail.</b>")


@app.on_message(filters.command("delthumbnail") & filters.private)
async def delthumbnail_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    await db.del_thumbnail(user_id)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🖼️ Set New", callback_data="menu_thumb"),
          InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )
    await message.reply_text("🗑️ <b>Thumbnail removed successfully!</b>", reply_markup=kb)


# ======================================================================= #
# Metadata
# ======================================================================= #

@app.on_message(filters.command("metadata") & filters.private)
async def metadata_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await safe_db_call(db.is_banned(user_id), default=False, label="is_banned"):
        return
    pending[user_id] = {"action": "await_metadata"}
    await message.reply_text(
        "📋 <b>Send metadata in format:</b>\n\n"
        "<code>Title | Artist | Album | Year</code>\n\n"
        "Example: <code>My Video | John Doe | My Album | 2024</code>"
    )


@app.on_message(filters.command("delmetadata") & filters.private)
async def delmetadata_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    await db.del_metadata(user_id)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Set New", callback_data="menu_meta"),
          InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )
    await message.reply_text("🗑️ <b>Metadata removed!</b>", reply_markup=kb)


# ======================================================================= #
# Caption
# ======================================================================= #

@app.on_message(filters.command("caption") & filters.private)
async def caption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await safe_db_call(db.is_banned(user_id), default=False, label="is_banned"):
        return
    pending[user_id] = {"action": "await_caption"}
    await message.reply_text(
        "✍️ <b>Send your permanent caption text</b>\n\n"
        "You can use:\n"
        "• <code>{filename}</code> - for file name\n"
        "• <code>{size}</code> - for file size\n"
        "• HTML formatting allowed"
    )


@app.on_message(filters.command("delcaption") & filters.private)
async def delcaption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    await db.del_caption(user_id)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✍️ Set New", callback_data="menu_caption"),
          InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )
    await message.reply_text("🗑️ <b>Caption removed!</b>", reply_markup=kb)


# ======================================================================= #
# Prefix / Suffix
# ======================================================================= #

@app.on_message(filters.command("prefix") & filters.private)
async def prefix_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    pending[user_id] = {"action": "await_prefix"}
    await message.reply_text(
        "🏷️ <b>Send prefix text to add BEFORE filename</b>\n\n"
        "Example: <code>[TeamName]_</code>\n"
        "Result: <code>[TeamName]_filename.mkv</code>"
    )


@app.on_message(filters.command("suffix") & filters.private)
async def suffix_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    pending[user_id] = {"action": "await_suffix"}
    await message.reply_text(
        "🏷️ <b>Send suffix text to add AFTER filename</b>\n\n"
        "Example: <code>@TeamName</code>\n"
        "Result: <code>filename@TeamName.mkv</code>"
    )


# ======================================================================= #
# Admin: /stats /broadcast /ban /unban
# ======================================================================= #

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("🚫 This command is for admins only.")

    total_users = await db.total_users_count()
    active_today = await db.active_today_count()
    banned = await db.banned_count()

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    text = (
        "<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
        "<b>👥 Users:</b>\n"
        f"• Total: {total_users}\n"
        f"• Active Today: {active_today}\n"
        f"• Banned: {banned}\n\n"
        "<b>💻 System:</b>\n"
        f"• CPU: {cpu}%\n"
        f"• RAM: {humanbytes(ram.used)}/{humanbytes(ram.total)} ({ram.percent}%)\n"
        f"• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)\n\n"
        "<b>⚡ Bot:</b>\n"
        f"• Uptime: {time_formatter(time.time() - START_TIME)}\n"
        f"• Workers: {config.PYROGRAM_WORKERS}\n"
        f"• Queue: {queue_depth} pending\n\n"
        "<b>🗄️ Database:</b>\n"
        "• Status: Connected ✅\n"
        "• Database: MongoDB"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats"),
          InlineKeyboardButton("📨 Broadcast", callback_data="open_broadcast")],
         [InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
    )
    await message.reply_text(text, reply_markup=kb)


@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return
    pending[message.from_user.id] = {"action": "await_broadcast"}
    await message.reply_text(
        "📨 <b>Send message to broadcast:</b>\n\nSend any text, photo, video, or document"
    )


@app.on_message(filters.command("ban") & filters.private)
async def ban_cmd(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply_text("Usage: <code>/ban user_id</code>")
    try:
        target = int(parts[1])
    except ValueError:
        return await message.reply_text("Invalid user id.")
    await db.ban_user(target)
    await message.reply_text(f"🚫 <b>User {target} banned!</b>")
    try:
        await client.send_message(target, "⚠️ <b>You have been banned from using this bot!</b>")
    except Exception:
        pass


@app.on_message(filters.command("unban") & filters.private)
async def unban_cmd(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply_text("Usage: <code>/unban user_id</code>")
    try:
        target = int(parts[1])
    except ValueError:
        return await message.reply_text("Invalid user id.")
    await db.unban_user(target)
    await message.reply_text(f"✅ <b>User {target} unbanned!</b>")
    try:
        await client.send_message(target, "✅ <b>You have been unbanned! You can use the bot now.</b>")
    except Exception:
        pass


async def run_broadcast(client: Client, admin_msg: Message, source_message: Message):
    user_ids = await db.get_all_user_ids()
    total = len(user_ids)
    sent = failed = 0
    status = await admin_msg.reply_text(f"📨 <b>Broadcasting...</b>\n\n✅ Sent: 0\n❌ Failed: 0\n📊 Progress: 0/{total}")

    for i, uid in enumerate(user_ids, 1):
        try:
            await source_message.copy(uid)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await source_message.copy(uid)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

        if i % 20 == 0 or i == total:
            try:
                await status.edit_text(
                    f"📨 <b>Broadcasting...</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}\n📊 Progress: {i}/{total}"
                )
            except Exception:
                pass

    await status.edit_text(f"✅ <b>Broadcast Complete!</b>\n\n✅ Successful: {sent}\n❌ Failed: {failed}")


# ======================================================================= #
# Text message router (handles "pending" conversational replies)
# ======================================================================= #

@app.on_message(filters.text & filters.private & ~filters.command([
    "start", "help", "about", "info", "settings", "thumbnail", "delthumbnail",
    "metadata", "delmetadata", "caption", "delcaption", "prefix", "suffix",
    "ping", "stats", "broadcast", "ban", "unban", "batch",
]))
async def text_router(client: Client, message: Message):
    user_id = message.from_user.id
    state = pending.get(user_id)

    # ---- Rename reply (file was sent, user replies with new filename) ---
    if message.reply_to_message and message.reply_to_message.id in reply_rename_map:
        await handle_rename_reply(client, message)
        return

    if not state:
        return  # nothing pending, ignore stray text

    action = state["action"]

    if action == "await_metadata":
        parts = [p.strip() for p in message.text.split("|")]
        title = parts[0] if len(parts) > 0 else None
        artist = parts[1] if len(parts) > 1 else None
        album = parts[2] if len(parts) > 2 else None
        year = parts[3] if len(parts) > 3 else None
        await db.set_metadata(user_id, title, artist, album, year)
        pending.pop(user_id, None)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_meta"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_text(
            f"✅ <b>Metadata set:</b>\n• Title: {title}\n• Artist: {artist}\n• Album: {album}\n• Year: {year}",
            reply_markup=kb,
        )

    elif action == "await_caption":
        await db.set_caption(user_id, message.text)
        pending.pop(user_id, None)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_caption"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_text(f"✅ <b>Caption saved!</b>\n\nPreview:\n{message.text}", reply_markup=kb)

    elif action == "await_prefix":
        await db.set_prefix(user_id, message.text.strip())
        pending.pop(user_id, None)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_prefix"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_text(f"✅ <b>Prefix set:</b> <code>{message.text.strip()}</code>", reply_markup=kb)

    elif action == "await_suffix":
        await db.set_suffix(user_id, message.text.strip())
        pending.pop(user_id, None)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_suffix"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_text(f"✅ <b>Suffix set:</b> <code>{message.text.strip()}</code>", reply_markup=kb)

    elif action == "await_broadcast":
        pending.pop(user_id, None)
        total_users = await db.total_users_count()
        state["broadcast_source"] = message
        confirm_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Send", callback_data=f"bc_send"),
              InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")]]
        )
        pending[f"bc_{user_id}"] = message  # stash source message for confirm callback
        await message.reply_text(
            f"📨 <b>Broadcast Preview:</b>\n\nSend this to {total_users} users?",
            reply_markup=confirm_kb,
        )

    elif action == "await_batch_pattern":
        pending.pop(user_id, None)
        await process_batch(client, message, state["files"], message.text.strip())

    elif action == "await_screenshot_count":
        pending.pop(user_id, None)
        try:
            count = int(message.text.strip())
        except ValueError:
            count = 4
        await run_screenshot_job(client, message, state["file_key"], count)

    elif action == "await_sample_duration":
        pending.pop(user_id, None)
        try:
            dur = int(message.text.strip())
        except ValueError:
            dur = 60
        await run_media_job(client, message, state["file_key"], "sample", extra=dur)


# ======================================================================= #
# File intake -> rename flow
# ======================================================================= #

reply_rename_map = {}  # incoming_file_message_id -> file_message


@app.on_message((filters.document | filters.video | filters.audio | filters.animation) & filters.private)
async def file_intake(client: Client, message: Message):
    user_id = message.from_user.id
    if await safe_db_call(db.is_banned(user_id), default=False, label="is_banned"):
        return

    state = pending.get(user_id)
    if state and state.get("action") == "await_thumbnail" and message.photo:
        return  # handled by photo_intake

    if state and state.get("action") == "batch_collecting":
        state["files"].append(message)
        await message.reply_text(f"➕ Added to batch. Total files: {len(state['files'])}\n\nSend more, or reply /donebatch to finish.")
        return

    media = get_media(message)
    if media and media.file_size and media.file_size > config.MAX_FILE_SIZE:
        await message.reply_text("❌ File too large. Max supported size is 4GB.")
        return

    filename = media_filename(message)
    size = media.file_size if media else 0
    duration = getattr(media, "duration", None)

    text = (
        "<b>📁 File Received!</b>\n\n"
        f"📄 <b>Name:</b> <code>{filename}</code>\n"
        f"📦 <b>Size:</b> {humanbytes(size)}\n"
    )
    if duration:
        text += f"⏱️ <b>Duration:</b> {time_formatter(duration)}\n"
    text += (
        "\n<b>✏️ Reply with new filename + extension</b>\n"
        "Example: <code>My New Video.mkv</code>\n\n"
        "<b>🔧 Available formats:</b>\n"
        f"Video: {', '.join('.' + f for f in config.SUPPORTED_VIDEO_FORMATS)}\n"
        f"Audio: {', '.join('.' + f for f in config.SUPPORTED_AUDIO_FORMATS)}"
    )

    file_key = register_file(message)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎬 Media Tools", callback_data=f"open_media_{file_key}"),
          InlineKeyboardButton("🔄 Convert", callback_data=f"open_convert_{file_key}")],
         [InlineKeyboardButton("❌ Cancel", callback_data="noop_cancel")]]
    )
    sent = await message.reply_text(text, reply_markup=kb)
    reply_rename_map[sent.id] = message
    # Also allow directly replying to the original file message
    reply_rename_map[message.id] = message


@app.on_message(filters.photo & filters.private)
async def photo_intake(client: Client, message: Message):
    user_id = message.from_user.id
    state = pending.get(user_id)
    if state and state.get("action") == "await_thumbnail":
        await db.set_thumbnail(user_id, message.photo.file_id)
        pending.pop(user_id, None)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Remove", callback_data="del_thumb"),
              InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
        )
        await message.reply_text("✅ <b>Permanent thumbnail set successfully!</b>", reply_markup=kb)


async def handle_rename_reply(client: Client, message: Message):
    user_id = message.from_user.id
    file_message = reply_rename_map.get(message.reply_to_message.id)
    if not file_message:
        return

    new_name = message.text.strip()
    if "." not in new_name:
        await message.reply_text("❌ Please include a file extension, e.g. <code>MyFile.mkv</code>")
        return

    ext = new_name.rsplit(".", 1)[-1].lower()
    if ext not in VIDEO_EXTS | AUDIO_EXTS and ext not in {"pdf", "zip", "rar", "txt", "jpg", "png", "apk"}:
        await message.reply_text(f"❌ Unsupported extension: .{ext}")
        return

    await process_rename(client, message, file_message, new_name)


async def process_rename(client: Client, trigger_message: Message, file_message: Message, new_name: str):
    global queue_depth
    user_id = trigger_message.from_user.id
    user = await safe_db_call(db.get_user(user_id), default=dict(EMPTY_USER), label="get_user")

    stop_token = uuid.uuid4().hex[:12]
    cancel_event = asyncio.Event()
    cancel_events[stop_token] = cancel_event

    status = await trigger_message.reply_text("**Task Running: 1/1**\n\nInitializing...", reply_markup=cancel_keyboard(stop_token))
    queue_depth += 1

    download_path = os.path.join(config.DOWNLOAD_DIR, f"{uuid.uuid4().hex}_{new_name}")

    try:
        tracker = ProgressTracker(status, new_name, "Download", stop_token, cancel_event)
        downloaded = await client.download_media(
            file_message, file_name=download_path, progress=tracker.update
        )

        final_name = build_final_name(new_name, user)
        final_path = await apply_user_customizations(downloaded, user)

        thumb = user.get("thumbnail")
        caption = build_caption(final_name, os.path.getsize(final_path), user)

        tracker2 = ProgressTracker(status, final_name, "Upload", stop_token, cancel_event)

        ext = final_name.rsplit(".", 1)[-1].lower()
        if ext in VIDEO_EXTS:
            await client.send_video(
                trigger_message.chat.id,
                final_path,
                thumb=thumb,
                caption=caption,
                file_name=final_name,
                progress=tracker2.update,
            )
        elif ext in AUDIO_EXTS:
            await client.send_audio(
                trigger_message.chat.id,
                final_path,
                thumb=thumb,
                caption=caption,
                file_name=final_name,
                progress=tracker2.update,
            )
        else:
            await client.send_document(
                trigger_message.chat.id,
                final_path,
                thumb=thumb,
                caption=caption,
                file_name=final_name,
                progress=tracker2.update,
            )

        await status.delete()
        await safe_db_call(db.increment_processed(user_id), default=None, label="increment_processed")

    except asyncio.CancelledError:
        await status.edit_text("❌ <b>Process cancelled!</b>")
    except Exception as e:
        log.exception("Rename processing failed")
        await status.edit_text(f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>")
    finally:
        queue_depth = max(0, queue_depth - 1)
        cancel_events.pop(stop_token, None)
        for p in {download_path}:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# ======================================================================= #
# Batch rename
# ======================================================================= #

@app.on_message(filters.command("batch") & filters.private)
async def batch_start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    pending[user_id] = {"action": "batch_collecting", "files": []}
    await message.reply_text(
        "<b>📦 Batch mode started!</b>\n\nSend files one by one, then send /donebatch when finished."
    )


@app.on_message(filters.command("donebatch") & filters.private)
async def batch_done_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    state = pending.get(user_id)
    if not state or state.get("action") != "batch_collecting" or not state["files"]:
        await message.reply_text("⚠️ No batch in progress. Use /batch to start one.")
        return

    count = len(state["files"])
    total_size = sum((get_media(f).file_size or 0) for f in state["files"])
    pending[user_id] = {"action": "await_batch_pattern", "files": state["files"]}
    await message.reply_text(
        "<b>📦 Batch Files Received!</b>\n"
        f"📁 <b>Files Count:</b> {count}\n"
        f"📦 <b>Total Size:</b> {humanbytes(total_size)}\n\n"
        "<b>✏️ Reply with:</b>\n"
        "<code>New Name</code> - for sequential naming\n"
        "or\n"
        "<code>New Name {n}</code> - for numbered files"
    )


async def process_batch(client: Client, trigger_message: Message, files: list, pattern: str):
    status = await trigger_message.reply_text(f"**Batch Task Running: 0/{len(files)}**")
    for i, file_message in enumerate(files, 1):
        if "{n}" in pattern:
            name = pattern.replace("{n}", str(i))
        else:
            base, ext = os.path.splitext(media_filename(file_message))
            orig_ext = ext or ".mp4"
            name = f"{pattern} {i}{orig_ext}" if len(files) > 1 else f"{pattern}{orig_ext}"
        try:
            await status.edit_text(f"**Batch Task Running: {i}/{len(files)}**\n\nProcessing: {name}")
        except Exception:
            pass
        await process_rename(client, trigger_message, file_message, name)
    await status.edit_text(f"✅ <b>Batch complete! {len(files)} files processed.</b>")


# ======================================================================= #
# Media tools job runner
# ======================================================================= #

async def run_media_job(client: Client, message: Message, file_key: str, op: str, extra=None):
    file_message = file_registry.get(file_key)
    if not file_message:
        await message.reply_text("⚠️ This file session expired. Please resend the file.")
        return

    stop_token = uuid.uuid4().hex[:12]
    cancel_event = asyncio.Event()
    cancel_events[stop_token] = cancel_event
    status = await message.reply_text("**Task Running: 1/1**\n\nDownloading source file...", reply_markup=cancel_keyboard(stop_token))

    download_path = os.path.join(config.DOWNLOAD_DIR, f"{uuid.uuid4().hex}_{media_filename(file_message)}")
    output_path = None
    try:
        tracker = ProgressTracker(status, media_filename(file_message), "Download", stop_token, cancel_event)
        await client.download_media(file_message, file_name=download_path, progress=tracker.update)

        await status.edit_text("⚙️ <b>Processing with ffmpeg...</b>")

        if op == "streamex":
            output_path = await ffmpeg_utils.stream_extract(download_path)
        elif op == "streamrm":
            output_path = await ffmpeg_utils.stream_remove(download_path)
        elif op == "audioex":
            output_path = await ffmpeg_utils.audio_extract(download_path, extra or "mp3")
        elif op == "audiorm":
            output_path = await ffmpeg_utils.audio_remove(download_path)
        elif op == "subex":
            output_path = await ffmpeg_utils.subtitle_extract(download_path)
        elif op == "subrm":
            output_path = await ffmpeg_utils.subtitle_remove(download_path)
        elif op == "sample":
            output_path = await ffmpeg_utils.sample_video(download_path, extra or 60)
        elif op == "compress":
            output_path = await ffmpeg_utils.compress_video(download_path)
        elif op == "convert_video":
            output_path = await ffmpeg_utils.convert_video(download_path, extra)
        elif op == "convert_audio":
            output_path = await ffmpeg_utils.convert_audio(download_path, extra)
        else:
            raise ValueError(f"Unknown op {op}")

        tracker2 = ProgressTracker(status, os.path.basename(output_path), "Upload", stop_token, cancel_event)
        await client.send_document(
            message.chat.id, output_path, caption=f"✅ <code>{os.path.basename(output_path)}</code>",
            progress=tracker2.update,
        )
        await status.delete()
        await safe_db_call(db.increment_processed(message.from_user.id), default=None, label="increment_processed")

    except ffmpeg_utils.FFmpegError as e:
        await status.edit_text(f"❌ <b>ffmpeg error:</b>\n<code>{str(e)[:300]}</code>")
    except asyncio.CancelledError:
        await status.edit_text("❌ <b>Process cancelled!</b>")
    except Exception as e:
        log.exception("Media job failed")
        await status.edit_text(f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>")
    finally:
        cancel_events.pop(stop_token, None)
        for p in (download_path, output_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def run_screenshot_job(client: Client, message: Message, file_key: str, count: int):
    file_message = file_registry.get(file_key)
    if not file_message:
        await message.reply_text("⚠️ This file session expired. Please resend the file.")
        return

    status = await message.reply_text("📸 <b>Generating screenshots...</b>")
    download_path = os.path.join(config.DOWNLOAD_DIR, f"{uuid.uuid4().hex}_{media_filename(file_message)}")
    paths = []
    try:
        await client.download_media(file_message, file_name=download_path)
        paths = await ffmpeg_utils.generate_screenshots(download_path, count=count)
        media_group = [__import__("pyrogram").types.InputMediaPhoto(p) for p in paths]
        await client.send_media_group(message.chat.id, media_group)
        await status.delete()
    except Exception as e:
        log.exception("Screenshot job failed")
        await status.edit_text(f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>")
    finally:
        for p in [download_path] + paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# ======================================================================= #
# Callback query router
# ======================================================================= #

@app.on_callback_query()
async def callback_router(client: Client, cq: CallbackQuery):
    data = cq.data
    user_id = cq.from_user.id

    try:
        if data == "open_home":
            await cq.message.edit_text(START_TXT.format(cq.from_user.mention), reply_markup=home_keyboard())

        elif data == "open_help":
            await cq.message.edit_text(HELP_TXT, reply_markup=back_home_kb(), disable_web_page_preview=True)

        elif data == "open_about":
            me = await client.get_me()
            await cq.message.edit_text(format_about(me.first_name), reply_markup=back_home_kb(), disable_web_page_preview=True)

        elif data == "open_settings":
            u = await safe_db_call(db.get_user(user_id), default=dict(EMPTY_USER), label="get_user")
            text = (
                "<b>⚙️ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ sᴇᴛᴛɪɴɢs</b>\n\n"
                f"🖼️ <b>Thumbnail:</b> {'✅ Set' if u.get('thumbnail') else '❌ Not Set'}\n"
                f"📋 <b>Metadata:</b> {'✅ Set' if u.get('metadata_title') else '❌ Not Set'}\n"
                f"✍️ <b>Caption:</b> {'✅ Set' if u.get('caption') else '❌ Not Set'}\n"
                f"🏷️ <b>Prefix:</b> {u.get('prefix') or 'None'}\n"
                f"🏷️ <b>Suffix:</b> {u.get('suffix') or 'None'}\n\n"
                "<b>📊 Stats:</b>\n"
                f"📁 <b>Files Processed:</b> {u.get('total_processed', 0)}"
            )
            await cq.message.edit_text(text, reply_markup=settings_keyboard())

        elif data == "open_stats" or data == "refresh_stats":
            if not is_admin(user_id):
                await cq.answer("Admins only.", show_alert=True)
                return
            total_users = await db.total_users_count()
            active_today = await db.active_today_count()
            banned = await db.banned_count()
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            text = (
                "<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
                "<b>👥 Users:</b>\n"
                f"• Total: {total_users}\n• Active Today: {active_today}\n• Banned: {banned}\n\n"
                "<b>💻 System:</b>\n"
                f"• CPU: {cpu}%\n• RAM: {humanbytes(ram.used)}/{humanbytes(ram.total)} ({ram.percent}%)\n"
                f"• Disk: {humanbytes(disk.used)}/{humanbytes(disk.total)} ({disk.percent}%)\n\n"
                "<b>⚡ Bot:</b>\n"
                f"• Uptime: {time_formatter(time.time() - START_TIME)}\n• Workers: {config.PYROGRAM_WORKERS}\n"
                f"• Queue: {queue_depth} pending\n\n"
                "<b>🗄️ Database:</b>\n• Status: Connected ✅\n• Database: MongoDB"
            )
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats"),
                  InlineKeyboardButton("📨 Broadcast", callback_data="open_broadcast")],
                 [InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
            )
            await cq.message.edit_text(text, reply_markup=kb)

        elif data == "refresh_info":
            u = await safe_db_call(db.get_user(user_id), default=dict(EMPTY_USER), label="get_user")
            user = cq.from_user
            caption = (
                "<b>📋 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>\n\n"
                f"➲ <b>First Name:</b> {user.first_name or ''}\n"
                f"➲ <b>Telegram ID:</b> <code>{user.id}</code>\n"
                f"➲ <b>User Name:</b> @{user.username or 'None'}\n\n"
                f"➲ <b>Files Processed:</b> {u.get('total_processed', 0)}"
            )
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_info"),
                  InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
            )
            try:
                await cq.message.edit_caption(caption, reply_markup=kb)
            except Exception:
                await cq.message.edit_text(caption, reply_markup=kb)

        elif data == "refresh_ping":
            start = time.time()
            await cq.answer("Pinging...")
            ping_time = (time.time() - start) * 1000
            uptime = time_formatter(time.time() - START_TIME)
            text = (
                "<b>🏓 ᴘᴏɴɢ!</b>\n\n"
                f"<b>⏱️ Response Time:</b> <code>{ping_time:.2f}ms</code>\n"
                f"<b>📡 Bot Uptime:</b> <code>{uptime}</code>\n"
                f"<b>⚡ Workers:</b> <code>{config.PYROGRAM_WORKERS}</code>\n"
                f"<b>🗄️ Database:</b> <code>Connected ✅</code>"
            )
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_ping"),
                  InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
            )
            await cq.message.edit_text(text, reply_markup=kb)

        elif data == "menu_thumb":
            pending[user_id] = {"action": "await_thumbnail"}
            await cq.message.reply_text("🖼️ <b>Please send a photo to set as your permanent thumbnail.</b>")

        elif data == "del_thumb":
            await db.del_thumbnail(user_id)
            await cq.answer("Thumbnail removed", show_alert=True)

        elif data == "menu_meta":
            pending[user_id] = {"action": "await_metadata"}
            await cq.message.reply_text(
                "📋 <b>Send metadata in format:</b>\n\n<code>Title | Artist | Album | Year</code>"
            )

        elif data == "del_meta":
            await db.del_metadata(user_id)
            await cq.answer("Metadata removed", show_alert=True)

        elif data == "menu_caption":
            pending[user_id] = {"action": "await_caption"}
            await cq.message.reply_text("✍️ <b>Send your permanent caption text</b>")

        elif data == "del_caption":
            await db.del_caption(user_id)
            await cq.answer("Caption removed", show_alert=True)

        elif data == "menu_prefsuf":
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏷️ Prefix", callback_data="menu_prefix"),
                  InlineKeyboardButton("🏷️ Suffix", callback_data="menu_suffix")],
                 [InlineKeyboardButton("🏠 Home", callback_data="open_home")]]
            )
            await cq.message.edit_text("<b>🏷️ Prefix / Suffix</b>\n\nChoose which one to set:", reply_markup=kb)

        elif data == "menu_prefix":
            pending[user_id] = {"action": "await_prefix"}
            await cq.message.reply_text("🏷️ <b>Send prefix text to add BEFORE filename</b>")

        elif data == "del_prefix":
            await db.del_prefix(user_id)
            await cq.answer("Prefix removed", show_alert=True)

        elif data == "menu_suffix":
            pending[user_id] = {"action": "await_suffix"}
            await cq.message.reply_text("🏷️ <b>Send suffix text to add AFTER filename</b>")

        elif data == "del_suffix":
            await db.del_suffix(user_id)
            await cq.answer("Suffix removed", show_alert=True)

        elif data == "how_rename":
            await cq.answer()
            await cq.message.reply_text(
                "📝 Send any file, then reply to the bot's message with the new filename + extension."
            )

        elif data == "how_batch":
            await cq.answer()
            await cq.message.reply_text("📦 Use /batch to start, send files, then /donebatch to finish.")

        elif data == "how_media":
            await cq.answer()
            await cq.message.reply_text("🎬 Send a file first, then tap the 'Media Tools' button under it.")

        elif data.startswith("open_media_"):
            file_key = data.replace("open_media_", "")
            await cq.message.reply_text("<b>🎬 Media Tools</b>\n\nSelect operation:", reply_markup=media_tools_keyboard(file_key))

        elif data.startswith("open_convert_"):
            file_key = data.replace("open_convert_", "")
            await cq.message.reply_text(
                "<b>🔄 Format Conversion</b>\n\n<b>🎬 Video / 🎵 Audio Formats:</b>",
                reply_markup=convert_keyboard(file_key),
            )

        elif data.startswith("mt_"):
            _, op_short, file_key = data.split("_", 2)
            op_map = {
                "streamex": "streamex", "streamrm": "streamrm",
                "audioex": "audioex", "audiorm": "audiorm",
                "subex": "subex", "subrm": "subrm",
                "compress": "compress",
            }
            if op_short == "ss":
                pending[user_id] = {"action": "await_screenshot_count", "file_key": file_key}
                await cq.message.reply_text("📸 <b>How many screenshots do you want?</b> (send a number, e.g. 4)")
            elif op_short == "sample":
                pending[user_id] = {"action": "await_sample_duration", "file_key": file_key}
                await cq.message.reply_text("✂️ <b>Sample duration in seconds?</b> (default 60)")
            elif op_short in op_map:
                await cq.answer("Starting...")
                asyncio.create_task(run_media_job(client, cq.message, file_key, op_map[op_short]))

        elif data.startswith("cv_"):
            _, fmt, file_key = data.split("_", 2)
            await cq.answer("Starting conversion...")
            asyncio.create_task(run_media_job(client, cq.message, file_key, "convert_video", extra=fmt))

        elif data.startswith("ca_"):
            _, fmt, file_key = data.split("_", 2)
            await cq.answer("Starting conversion...")
            asyncio.create_task(run_media_job(client, cq.message, file_key, "convert_audio", extra=fmt))

        elif data.startswith("cancel_"):
            stop_token = data.replace("cancel_", "")
            event = cancel_events.get(stop_token)
            if event:
                event.set()
                await cq.answer("Cancelling...")
            else:
                await cq.answer("Nothing to cancel.")

        elif data == "noop_cancel":
            await cq.answer("File request cancelled.")
            try:
                await cq.message.delete()
            except Exception:
                pass

        elif data == "open_broadcast":
            if not is_admin(user_id):
                return await cq.answer("Admins only.", show_alert=True)
            pending[user_id] = {"action": "await_broadcast"}
            await cq.message.reply_text("📨 <b>Send message to broadcast:</b>")

        elif data == "bc_send":
            source = pending.pop(f"bc_{user_id}", None)
            if source:
                await cq.answer("Broadcasting...")
                asyncio.create_task(run_broadcast(client, cq.message, source))
            else:
                await cq.answer("Nothing to broadcast.")

        elif data == "bc_cancel":
            pending.pop(f"bc_{user_id}", None)
            await cq.answer("Broadcast cancelled.")
            await cq.message.edit_text("❌ Broadcast cancelled.")

        else:
            await cq.answer()

    except Exception as e:
        log.exception("Callback handling failed")
        try:
            await cq.answer(f"Error: {str(e)[:150]}", show_alert=True)
        except Exception:
            pass


# ======================================================================= #
# Health check server (for Koyeb / other platforms that require an open
# HTTP port to consider the service "healthy")
# ======================================================================= #

HEALTH_CHECK_PORT = int(os.environ.get("PORT", 8000))


async def health(request):
    return web.Response(text="OK")


async def start_health_server():
    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_CHECK_PORT)
    await site.start()
    log.info(f"Health check server running on port {HEALTH_CHECK_PORT}")
    return runner


# ======================================================================= #
# Entrypoint
# ======================================================================= #

async def main():
    log.info("Starting Rename Bot...")
    runner = await start_health_server()
    await app.start()
    me = await app.get_me()
    log.info(f"Bot started as @{me.username} (id={me.id}).")
    try:
        await idle()
    finally:
        await app.stop()
        await runner.cleanup()
        log.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
