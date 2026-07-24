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
TASK_QUEUE_COUNT = 9 

USER_STATES = {}

AVAILABLE_TOOLS = {
    "rem_stream": "❌ Stream Remove",
    "ext_stream": "📤 Stream Extract",
    "ext_audio": "🎵 Extract Audio",
    "ext_sub": "💬 Extract Subtitle",
    "screenshot": "📸 Take Screenshot",
    "sample": "🎞️ Sample Video"
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
    tmp = ((f"{days}d " if days else "") +
           (f"{hours}h " if hours else "") +
           (f"{minutes}m " if minutes else "") +
           (f"{seconds}s" if seconds else ""))
    return tmp if tmp else "0s"

def get_random_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def handle_koyeb_healthcheck(request):
    return web.Response(text="Bot Active", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_koyeb_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()

# Customized UI matching target layout
async def progress_for_pyrogram(current, total, ud_type, message, start, task_id, filename, user_name, user_id):
    if task_id in CANCEL_TASKS:
        bot.stop_transmission()
        return

    now = time.time()
    diff = now - start
    if diff == 0:
        return

    if round(diff % 2.00) == 0 or current == total:
        percentage = (current * 100 / total) if total > 0 else 0
        speed = current / diff if diff > 0 else 0
        elapsed_sec = round(diff)
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0

        # Custom progress bar blocks
        filled_length = math.floor(percentage / 8.33) # 12 blocks length
        progress_bar = f"[{'█' * filled_length}{'░' * (12 - filled_length)}]"

        tmp = (
            f"<b>Task Running: {TASK_QUEUE_COUNT}/20 ❞</b>\n\n"
            f"<b>1.{ud_type}:</b>\n"
            f"{progress_bar} {round(percentage)}%\n"
            f"<b>Processed:</b> {humanbytes(current)}\n"
            f"<b>Size:</b> {humanbytes(total)}\n"
            f"<b>Speed:</b> {humanbytes(speed)}/s\n"
            f"<b>ETA:</b> {time_formatter(time_to_completion) if current != total else '0s'}\n"
            f"<b>Elapsed:</b> {elapsed_sec}s\n"
            f"<b>Upload:</b> Telegram\n"
            f"<b>Engine:</b> Pyrogram v2.0\n"
            f"<b>{user_name}</b> (<code>{user_id}</code>)\n"
            f"/stop_{task_id}"
        )
        try:
            await message.edit(
                text=tmp,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_prog_{task_id}")]
                ])
            )
        except Exception:
            pass

async def probe_media_streams(file_path):
    """Retrieves all internal streams/tracks via ffprobe."""
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
        "<b>👋 Welcome to Interactive Media Editor Bot!</b>\n\n"
        "Send any video or file to begin."
    )

@bot.on_message(filters.private & filters.regex(r"^/stop_"))
async def stop_task_cmd(client: Client, message: Message):
    task_id = message.text.replace("/stop_", "").strip()
    CANCEL_TASKS.add(task_id)
    await message.reply(f"🛑 Cancel request received for task <code>{task_id}</code>.")

# STEP 1: Receive File -> Ask for New Name
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

# STEP 2: Receive New Name -> Show Checkmark Tool Selector
@bot.on_message(filters.private & filters.text & ~filters.command(["start"]) & ~filters.regex(r"^/stop_"))
async def on_text_receive(client: Client, message: Message):
    user_id = message.from_user.id
    state = USER_STATES.get(user_id)

    if not state or state.get("step") != "AWAITING_NAME":
        return await message.reply("Please send a video or media file first!")

    state["new_name"] = message.text.strip()
    state["step"] = "SELECTING_TOOLS"

    await message.reply(
        f"✏️ <b>New Name Set:</b> <code>{state['new_name']}</code>\n\n"
        f"🛠️ <b>Select the actions/tools you need:</b>\n"
        f"<i>(Tap options to toggle checkmark ✅, then click DONE)</i>",
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
            await query.answer("Please select at least one operation!", show_alert=True)
            return

        state["step"] = "DOWNLOADING"
        task_id = get_random_id()
        state["task_id"] = task_id

        user_name = query.from_user.first_name or "User"
        
        status_msg = await query.message.edit_text(
            f"<b>Task Running: {TASK_QUEUE_COUNT}/20 ❞</b>\n\n"
            f"<b>1.Download:</b>\n"
            f"[░░░░░░░░░░░░] 0%\n"
            f"<b>Processed:</b> 0B\n"
            f"<b>Size:</b> 0B\n"
            f"<b>Speed:</b> 0B/s\n"
            f"<b>ETA:</b> -\n"
            f"<b>Elapsed:</b> 0s\n"
            f"<b>Upload:</b> Telegram\n"
            f"<b>Engine:</b> Pyrogram v2.0\n"
            f"<b>{user_name}</b> (<code>{user_id}</code>)\n"
            f"/stop_{task_id}"
        )

        input_path = os.path.join(config.DOWNLOAD_DIR, f"{task_id}_input.mkv")
        state["input_path"] = input_path

        try:
            start_time = time.time()
            dl_path = await client.download_media(
                message=state["file_msg"],
                file_name=input_path,
                progress=progress_for_pyrogram,
                progress_args=("Download", status_msg, start_time, task_id, state["new_name"], user_name, user_id)
            )

            state["dl_path"] = dl_path
            streams = await probe_media_streams(dl_path)
            state["streams"] = streams

            if "rem_stream" in state["tools"] or "ext_stream" in state["tools"]:
                state["step"] = "SELECTING_STREAMS"
                await status_msg.edit(
                    "🎬 <b>Detected Tracks inside video:</b>\n"
                    "<i>Select checkmarks for streams to remove or extract, then tap Process & Upload!</i>",
                    reply_markup=build_streams_keyboard(streams, state["selected_streams"])
                )
            else:
                await process_and_upload(client, status_msg, user_id)

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

    elif data.startswith("refresh_prog_"):
        await query.answer("Progress Refreshed!", show_alert=False)

# Processing Engine
async def process_and_upload(client: Client, status_msg: Message, user_id: int):
    state = USER_STATES.get(user_id)
    if not state:
        return

    dl_path = state["dl_path"]
    new_name = state["new_name"]
    base_name, ext = os.path.splitext(new_name)
    if not ext:
        ext = ".mkv"
    task_id = state["task_id"]

    user_name = status_msg.chat.first_name or "User"

    output_files = [] # Stores file paths to upload

    try:
        async with ffmpeg_semaphore:
            # 1. Take Screenshot
            if "screenshot" in state["tools"]:
                ss_out_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}_screenshot.jpg")
                cmd_ss = ["ffmpeg", "-y", "-ss", "00:02:00", "-i", dl_path, "-vframes", "1", "-q:v", "2", ss_out_path]
                proc_ss = await asyncio.create_subprocess_exec(*cmd_ss, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc_ss.communicate()
                if os.path.exists(ss_out_path) and os.path.getsize(ss_out_path) > 0:
                    output_files.append(ss_out_path)

            # 2. Generate Sample Video (30 seconds clip)
            if "sample" in state["tools"]:
                sample_out_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}_sample{ext}")
                cmd_sample = ["ffmpeg", "-y", "-ss", "00:01:00", "-i", dl_path, "-t", "30", "-c", "copy", sample_out_path]
                proc_sample = await asyncio.create_subprocess_exec(*cmd_sample, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc_sample.communicate()
                if os.path.exists(sample_out_path) and os.path.getsize(sample_out_path) > 0:
                    output_files.append(sample_out_path)

            # 3. Extract Subtitle
            if "ext_sub" in state["tools"]:
                sub_out_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}.srt")
                cmd_sub = ["ffmpeg", "-y", "-i", dl_path, "-map", "0:s:0?", sub_out_path]
                proc_sub = await asyncio.create_subprocess_exec(*cmd_sub, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc_sub.communicate()
                if os.path.exists(sub_out_path) and os.path.getsize(sub_out_path) > 0:
                    output_files.append(sub_out_path)

            # 4. Extract Audio
            if "ext_audio" in state["tools"]:
                audio_out_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}.mp3")
                cmd_aud = ["ffmpeg", "-y", "-i", dl_path, "-vn", "-acodec", "libmp3lame", audio_out_path]
                proc_aud = await asyncio.create_subprocess_exec(*cmd_aud, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc_aud.communicate()
                if os.path.exists(audio_out_path) and os.path.getsize(audio_out_path) > 0:
                    output_files.append(audio_out_path)

            # 5. Main Media Video Output Processing
            main_output_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}{ext}")
            cmd_main = ["ffmpeg", "-y", "-i", dl_path]

            if "rem_stream" in state["tools"] and state["selected_streams"]:
                cmd_main.extend(["-map", "0"])
                for s_idx in state["selected_streams"]:
                    cmd_main.extend(["-map", f"-0:{s_idx}"])
                cmd_main.extend(["-c", "copy"])
            elif "ext_stream" in state["tools"] and state["selected_streams"]:
                for s_idx in state["selected_streams"]:
                    cmd_main.extend(["-map", f"0:{s_idx}"])
                cmd_main.extend(["-c", "copy"])
            else:
                cmd_main.extend(["-c", "copy"])

            cmd_main.append(main_output_path)

            proc_main = await asyncio.create_subprocess_exec(*cmd_main, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc_main.communicate()

            if os.path.exists(main_output_path) and os.path.getsize(main_output_path) > 0:
                output_files.append(main_output_path)

        # 6. Upload All Processed File(s)
        for target_file in output_files:
            target_filename = os.path.basename(target_file)
            upload_start = time.time()

            await client.send_document(
                chat_id=status_msg.chat.id,
                document=target_file,
                caption=f"<b>{target_filename}</b>",
                progress=progress_for_pyrogram,
                progress_args=("Upload", status_msg, upload_start, task_id, target_filename, user_name, user_id)
            )

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Processing Error: {e}")
        await status_msg.edit(f"❌ <b>Processing Failed:</b> <code>{str(e)}</code>")

    finally:
        USER_STATES.pop(user_id, None)
        CANCEL_TASKS.discard(task_id)
        if os.path.exists(dl_path):
            try: os.remove(dl_path)
            except Exception: pass
        for f in output_files:
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass

async def main():
    await start_web_server()
    await bot.start()
    logger.info("Bot running with full screenshot, sample video & subtitle extract tools.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
