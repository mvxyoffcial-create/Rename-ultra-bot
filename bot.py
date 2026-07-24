import os
import sys
import time
import math
import json
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
    CallbackQuery
)
from pyrogram.errors import FloodWait

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
db = mongo_client["interactive_media_bot"]
users_col = db["users"]

# Worker & Queue Setup
executor = ThreadPoolExecutor(max_workers=500)
ffmpeg_semaphore = asyncio.Semaphore(10)

bot = Client(
    "InteractiveMediaBot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=500
)

BOT_START_TIME = time.time()
CANCEL_TASKS = set()

# State Management Caches
USER_STATES = {}  # {user_id: {"step": ..., "file_msg": ..., "new_name": ..., "tools": set(), "stream_selections": set()}}

AVAILABLE_TOOLS = {
    "rem_stream": "Stream Remove",
    "ext_stream": "Stream Extract",
    "ext_audio": "Audio Extract",
    "rem_audio": "Audio Remove",
    "ext_sub": "Subtitle Extract",
    "rem_sub": "Subtitle Remove",
    "screenshot": "Screenshot",
    "sample": "Sample Video"
}

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

def get_random_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

async def handle_koyeb_healthcheck(request):
    return web.Response(text="Bot Active", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_koyeb_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()

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
        time_to_completion = round((total - current) / speed) * 1000

        progress = "[{0}{1}]".format(
            ''.join(["█" for _ in range(math.floor(percentage / 5))]),
            ''.join(["░" for _ in range(20 - math.floor(percentage / 5))])
        )

        tmp = (
            f"╔════════════════════════════════════╗\n"
            f"║ 📁 {ud_type}\n"
            f"║ {progress} {round(percentage, 2)}%\n"
            f"║ ⚡ Speed: {humanbytes(speed)}/s\n"
            f"║ 📦 {humanbytes(current)} / {humanbytes(total)}\n"
            f"║ ⏳ ETA: {time_formatter(time_to_completion)}\n"
            f"╚════════════════════════════════════╝"
        )
        try:
            await message.edit(
                text=tmp,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task_id}")]
                ])
            )
        except Exception:
            pass

async def probe_media_streams(file_path):
    """Analyzes video using ffprobe to retrieve stream details."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", file_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode('utf-8'))
    return data.get("streams", [])

def build_tools_keyboard(selected_tools):
    buttons = []
    keys = list(AVAILABLE_TOOLS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i+2]:
            mark = "✅ " if k in selected_tools else ""
            row.append(InlineKeyboardButton(f"{mark}{AVAILABLE_TOOLS[k]}", callback_data=f"toggle_tool_{k}"))
        buttons.append(row)
    
    buttons.append([InlineKeyboardButton("✅ DONE", callback_data="tools_done")])
    return InlineKeyboardMarkup(buttons)

def build_streams_keyboard(streams, selected_streams):
    buttons = []
    for s in streams:
        idx = s.get("index")
        codec_type = s.get("codec_type", "unknown").upper()
        codec_name = s.get("codec_name", "unknown")
        tags = s.get("tags", {})
        lang = tags.get("language", "und")

        label = f"Stream #{idx}: [{codec_type}] {codec_name} ({lang})"
        mark = "✅ " if idx in selected_streams else ""

        buttons.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"toggle_stream_{idx}")])

    buttons.append([InlineKeyboardButton("🚀 Process & Upload", callback_data="streams_done")])
    return InlineKeyboardMarkup(buttons)

@bot.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply(
        "<b>👋 Welcome to the Interactive Media Editor Bot!</b>\n\n"
        "Send any video or media file to start!"
    )

# STEP 1: User sends a file -> Bot asks for New Name
@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def on_file_receive(client: Client, message: Message):
    user_id = message.from_user.id
    
    media = message.document or message.video or message.audio
    filename = getattr(media, "file_name", "Video.mkv")

    USER_STATES[user_id] = {
        "step": "AWAITING_NAME",
        "file_msg": message,
        "original_name": filename,
        "new_name": filename,
        "tools": set(),
        "selected_streams": set()
    }

    await message.reply(
        f"📁 <b>File Received:</b> <code>{filename}</code>\n\n"
        f"📝 <b>Please reply with the NEW NAME for this file:</b>"
    )

# STEP 2: User provides New Name -> Bot shows Media Tools with Checkmarks
@bot.on_message(filters.private & filters.text & ~filters.command(["start"]))
async def on_text_receive(client: Client, message: Message):
    user_id = message.from_user.id
    state = USER_STATES.get(user_id)

    if not state or state.get("step") != "AWAITING_NAME":
        return await message.reply("Please send a file or video first!")

    state["new_name"] = message.text.strip()
    state["step"] = "SELECTING_TOOLS"

    await message.reply(
        f"✅ <b>New Name Set:</b> <code>{state['new_name']}</code>\n\n"
        f"🛠️ <b>Select the Media Tool operations you want to apply:</b>\n"
        f"<i>(Tap buttons to checkmark ✅ what you need, then click DONE)</i>",
        reply_markup=build_tools_keyboard(state["tools"])
    )

@bot.on_callback_query()
async def on_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data
    state = USER_STATES.get(user_id)

    await query.answer()

    if data.startswith("toggle_tool_") and state:
        tool_key = data.replace("toggle_tool_", "")
        if tool_key in state["tools"]:
            state["tools"].remove(tool_key)
        else:
            state["tools"].add(tool_key)

        await query.message.edit_reply_markup(reply_markup=build_tools_keyboard(state["tools"]))

    elif data == "tools_done" and state:
        if not state["tools"]:
            await query.answer("Please select at least one tool or operation!", show_alert=True)
            return

        state["step"] = "DOWNLOADING"
        status_msg = await query.message.edit_text("⏳ <b>Downloading video to analyze media streams...</b>")

        task_id = get_random_id()
        input_path = os.path.join(config.DOWNLOAD_DIR, f"{task_id}_input.mkv")
        state["task_id"] = task_id
        state["input_path"] = input_path

        try:
            start_time = time.time()
            dl_path = await client.download_media(
                message=state["file_msg"],
                file_name=input_path,
                progress=progress_for_pyrogram,
                progress_args=("Downloading...", status_msg, start_time, task_id)
            )

            state["dl_path"] = dl_path
            streams = await probe_media_streams(dl_path)
            state["streams"] = streams

            # STEP 3: Check if Stream Remove / Extract was chosen -> Show Stream List
            if "rem_stream" in state["tools"] or "ext_stream" in state["tools"]:
                state["step"] = "SELECTING_STREAMS"
                await status_msg.edit(
                    "🎬 <b>Select the Streams/Tracks to act upon:</b>\n"
                    "<i>(Select stream index checkmarks and click Process & Upload)</i>",
                    reply_markup=build_streams_keyboard(streams, state["selected_streams"])
                )
            else:
                await process_and_upload(client, query.message, user_id)

        except Exception as e:
            logger.error(f"Download Error: {e}")
            await status_msg.edit(f"❌ <b>Error:</b> <code>{str(e)}</code>")

    elif data.startswith("toggle_stream_") and state:
        stream_idx = int(data.replace("toggle_stream_", ""))
        if stream_idx in state["selected_streams"]:
            state["selected_streams"].remove(stream_idx)
        else:
            state["selected_streams"].add(stream_idx)

        await query.message.edit_reply_markup(reply_markup=build_streams_keyboard(state["streams"], state["selected_streams"]))

    elif data == "streams_done" and state:
        await process_and_upload(client, query.message, user_id)

    elif data.startswith("cancel_"):
        task_id = data.split("_")[1]
        CANCEL_TASKS.add(task_id)

async def process_and_upload(client: Client, status_msg: Message, user_id: int):
    state = USER_STATES.get(user_id)
    if not state:
        return

    dl_path = state["dl_path"]
    new_name = state["new_name"]
    output_path = os.path.join(config.DOWNLOAD_DIR, new_name)
    task_id = state["task_id"]

    await status_msg.edit("⚙️ <b>Processing FFmpeg operations...</b>")

    try:
        async with ffmpeg_semaphore:
            cmd = ["ffmpeg", "-y", "-i", dl_path]

            # Handle Stream Removals
            if "rem_stream" in state["tools"] and state["selected_streams"]:
                cmd.extend(["-map", "0"])
                for s_idx in state["selected_streams"]:
                    cmd.extend(["-map", f"-0:{s_idx}"])
                cmd.extend(["-c", "copy"])
            else:
                cmd.extend(["-c", "copy"])

            cmd.append(output_path)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

        await status_msg.edit("⚡ <b>Uploading processed file...</b>")
        start_time = time.time()

        await client.send_document(
            chat_id=status_msg.chat.id,
            document=output_path,
            caption=f"<b>{new_name}</b>",
            progress=progress_for_pyrogram,
            progress_args=("Uploading...", status_msg, start_time, task_id)
        )

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit(f"❌ <b>Processing Failed:</b> <code>{str(e)}</code>")

    finally:
        USER_STATES.pop(user_id, None)
        CANCEL_TASKS.discard(task_id)
        for target in [dl_path, output_path]:
            if os.path.exists(target):
                try: os.remove(target)
                except Exception: pass

async def main():
    await start_web_server()
    await bot.start()
    logger.info("Interactive Media Bot running.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
