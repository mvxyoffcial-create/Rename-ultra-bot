import os
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from handlers.rename import USER_STATES
from ffmpeg_tools import (
    get_media_streams, remove_streams_with_progress, extract_audio_with_progress, 
    take_screenshot, create_sample, extract_stream
)
from utils import progress_bar, PROGRESS_CACHE
from config import Config

STOPPED_TASKS = set()

def format_language(lang_code: str) -> str:
    lang_map = {
        "eng": "English 🇬🇧",
        "hin": "Hindi 🇮🇳",
        "tam": "Tamil 🇮🇳",
        "tel": "Telugu 🇮🇳",
        "mal": "Malayalam 🇮🇳",
        "kan": "Kannada 🇮🇳",
        "jpn": "Japanese 🇯🇵",
        "und": "Unknown 🌐"
    }
    return lang_map.get(lang_code.lower(), lang_code.upper())

def build_remove_menu_markup(streams: list, selected_indices: list):
    buttons = []
    for idx, s in enumerate(streams):
        codec = s.get("codec_name", "unknown")
        stype = s.get("codec_type", "unknown").capitalize()
        lang = format_language(s.get("tags", {}).get("language", "und"))
        mark = "✅" if idx in selected_indices else "⬜"
        btn_text = f"{mark} Stream {idx}: {stype} ({codec}) [{lang}]"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"select_rm_{idx}")])

    buttons.append([
        InlineKeyboardButton("🎵 All Audio", callback_data="select_rm_all_audio"),
        InlineKeyboardButton("📝 All Subtitles", callback_data="select_rm_all_subs")
    ])
    
    buttons.append([
        InlineKeyboardButton("✅ Confirm & Execute", callback_data="exec_stream_remove"),
        InlineKeyboardButton("Close ❌", callback_data="tool_action_close")
    ])
    return InlineKeyboardMarkup(buttons)

@Client.on_message(filters.regex(r"^/stop_"))
async def stop_task_handler(client: Client, message: Message):
    task_id = message.text.replace("/stop_", "").strip()
    STOPPED_TASKS.add(task_id)
    await message.reply_text(f"🛑 <b>Task {task_id} cancellation requested.</b>")

@Client.on_callback_query(filters.regex(r"^refresh_progress_"))
async def refresh_progress_cb(client: Client, callback_query: CallbackQuery):
    task_id = callback_query.data.replace("refresh_progress_", "")
    if task_id in PROGRESS_CACHE:
        cache = PROGRESS_CACHE[task_id]
        await progress_bar(
            current=cache["current"],
            total=cache["total"],
            status_type=cache["status_type"],
            message=callback_query.message,
            start_time=cache["start_time"],
            task_id=task_id,
            user=callback_query.from_user,
            force_update=True
        )
        await callback_query.answer("Progress Refreshed! 🔄")
    else:
        await callback_query.answer("Progress updated.", show_alert=False)

async def execute_processing(client: Client, user_id: int, message: Message):
    if user_id not in USER_STATES:
        return
    state = USER_STATES[user_id]
    
    raw_task_id = state["task_id"]
    task_id = raw_task_id.split("_")[-1]
    new_name = state["new_name"]
    file_msg = state["message"]
    
    status_msg = await message.reply_text("⏳ Initializing Task...")
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    local_path = state.get("local_path")
    
    # Force initial 0% progress display
    start_time = time.time()
    file_size = getattr(state["message"].video or state["message"].document, "file_size", 0)
    await progress_bar(0, file_size, "Download File", status_msg, start_time, task_id, message.from_user, force_update=True)

    # 1. Download File
    if not local_path or not os.path.exists(local_path):
        local_path = os.path.join(Config.DOWNLOAD_DIR, f"{raw_task_id}_{state['file_name']}")
        await client.download_media(
            message=file_msg,
            file_name=local_path,
            progress=progress_bar,
            progress_args=("Download File", status_msg, start_time, task_id, message.from_user)
        )

    output_path = os.path.join(Config.DOWNLOAD_DIR, new_name)
    actions = state.get("selected_actions", {})

    # 2. FFmpeg Processing
    if actions.get("remove") and state.get("remove_selected"):
        await remove_streams_with_progress(
            local_path, output_path, state["remove_selected"],
            progress_bar, status_msg, task_id, message.from_user
        )
    else:
        os.rename(local_path, output_path)

    outputs_to_upload = [(output_path, "video")]

    if actions.get("audio"):
        audio_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_audio.mp3")
        if await extract_audio_with_progress(output_path, audio_out, progress_bar, status_msg, task_id, message.from_user):
            outputs_to_upload.append((audio_out, "audio"))

    if actions.get("screenshot"):
        ss_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_screenshot.jpg")
        if await take_screenshot(output_path, ss_out):
            outputs_to_upload.append((ss_out, "photo"))

    if actions.get("sample"):
        sample_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_sample.mp4")
        if await create_sample(output_path, sample_out):
            outputs_to_upload.append((sample_out, "video"))

    # 3. Clean File Delivery
    for file_to_send, media_type in outputs_to_upload:
        if raw_task_id in STOPPED_TASKS or task_id in STOPPED_TASKS:
            await status_msg.edit_text("❌ Task Cancelled by user.")
            break

        start_time = time.time()
        if media_type == "video":
            await client.send_video(
                chat_id=message.chat.id,
                video=file_to_send,
                caption=f"<b>📄 File Name:</b> <code>{os.path.basename(file_to_send)}</code>",
                progress=progress_bar,
                progress_args=("Uploading File", status_msg, start_time, task_id, message.from_user)
            )
        elif media_type == "audio":
            await client.send_audio(
                chat_id=message.chat.id, 
                audio=file_to_send,
                caption=f"<b>🎵 Extracted Audio:</b> <code>{os.path.basename(file_to_send)}</code>"
            )
        elif media_type == "photo":
            await client.send_photo(
                chat_id=message.chat.id, 
                photo=file_to_send,
                caption=f"<b>📸 Captured Screenshot</b>"
            )

        if os.path.exists(file_to_send):
            os.remove(file_to_send)

    await status_msg.delete()
    if "menu_message_id" in state:
        try:
            await client.delete_messages(chat_id=message.chat.id, message_ids=state["menu_message_id"])
        except Exception:
            pass

    USER_STATES.pop(user_id, None)
