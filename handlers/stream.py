import os
import time
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
        "eng": "English 🇬🇧", "hin": "Hindi 🇮🇳", "tam": "Tamil 🇮🇳",
        "tel": "Telugu 🇮🇳", "mal": "Malayalam 🇮🇳", "kan": "Kannada 🇮🇳",
        "jpn": "Japanese 🇯🇵", "und": "Unknown 🌐"
    }
    return lang_map.get(str(lang_code).lower(), str(lang_code).upper())

def build_remove_menu_markup(streams: list, selected_indices: list):
    buttons = []
    for idx, s in enumerate(streams):
        codec = s.get("codec_name", "unknown")
        stype = s.get("codec_type", "unknown").capitalize()
        lang = format_language(s.get("tags", {}).get("language", "und"))
        mark = "✅" if idx in selected_indices else "⬜"
        buttons.append([InlineKeyboardButton(f"{mark} Stream {idx}: {stype} ({codec}) [{lang}]", callback_data=f"select_rm_{idx}")])

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
    await message.reply_text(f"🛑 Task {task_id} cancellation requested.")

@Client.on_callback_query(filters.regex(r"^refresh_progress_"))
async def refresh_progress_cb(client: Client, callback_query: CallbackQuery):
    task_id = callback_query.data.replace("refresh_progress_", "")
    if task_id in PROGRESS_CACHE:
        cache = PROGRESS_CACHE[task_id]
        await progress_bar(
            current=cache["current"], total=cache["total"], status_type=cache["status_type"],
            message=callback_query.message, start_time=cache["start_time"], task_id=task_id,
            user=callback_query.from_user, force_update=True
        )
        await callback_query.answer("Progress Refreshed! 🔄")
    else:
        await callback_query.answer("No active status found.", show_alert=True)

@Client.on_callback_query(filters.regex("^tool_action_done$"))
async def on_done_click(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Session expired!", show_alert=True)

    state = USER_STATES[user_id]
    actions = state.get("selected_actions", {})

    if not any(actions.values()):
        return await callback_query.answer("⚠️ Please select at least one action!", show_alert=True)

    if actions.get("extract"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video & audio streams...")
        file_msg = state["message"]
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        temp_path = os.path.join(Config.DOWNLOAD_DIR, f"temp_{user_id}_{state['file_name']}")
        
        start_time = time.time()
        dl_path = await client.download_media(
            message=file_msg, file_name=temp_path, progress=progress_bar,
            progress_args=("Downloading for Analysis", status_msg, start_time, state["task_id"], callback_query.from_user)
        )
        state["local_path"] = dl_path
        streams = await get_media_streams(dl_path)
        state["available_streams"] = streams
        
        buttons = []
        text = "<b>📤 Stream Extract Menu:</b>\n\nSelect stream to extract:\n"
        for idx, s in enumerate(streams):
            codec = s.get("codec_name", "unknown")
            stype = s.get("codec_type", "unknown").capitalize()
            lang = format_language(s.get("tags", {}).get("language", "und"))
            text += f"• <b>Stream {idx}:</b> {stype} ({codec}) - 🌐 {lang}\n"
            buttons.append([InlineKeyboardButton(f"Extract Stream {idx} ({stype} - {lang})", callback_data=f"exec_extract_{idx}")])
        
        buttons.append([InlineKeyboardButton("Close ❌", callback_data="tool_action_close")])
        return await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    if actions.get("remove"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video & audio streams...")
        file_msg = state["message"]
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        temp_path = os.path.join(Config.DOWNLOAD_DIR, f"temp_{user_id}_{state['file_name']}")
        
        start_time = time.time()
        dl_path = await client.download_media(
            message=file_msg, file_name=temp_path, progress=progress_bar,
            progress_args=("Downloading for Analysis", status_msg, start_time, state["task_id"], callback_query.from_user)
        )
        state["local_path"] = dl_path
        streams = await get_media_streams(dl_path)
        state["available_streams"] = streams
        state["remove_selected"] = []

        return await status_msg.edit_text("<b>🗑️ Stream Remover Menu:</b>", reply_markup=build_remove_menu_markup(streams, []))

    await execute_processing(client, user_id, callback_query.message)

@Client.on_callback_query(filters.regex("^select_rm_"))
async def select_stream_rm(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)
        
    state = USER_STATES[user_id]
    streams = state.get("available_streams", [])
    sel = state.get("remove_selected", [])
    action = callback_query.data.replace("select_rm_", "")

    if action == "all_audio":
        audio_indices = [idx for idx, s in enumerate(streams) if s.get("codec_type") == "audio"]
        sel = [] if all(idx in sel for idx in audio_indices) else list(set(sel + audio_indices))
    elif action == "all_subs":
        sub_indices = [idx for idx, s in enumerate(streams) if s.get("codec_type") in ["subtitle", "subrip"]]
        sel = [] if all(idx in sel for idx in sub_indices) else list(set(sel + sub_indices))
    else:
        idx = int(action)
        sel.remove(idx) if idx in sel else sel.append(idx)

    state["remove_selected"] = sel
    await callback_query.message.edit_reply_markup(reply_markup=build_remove_menu_markup(streams, sel))

@Client.on_callback_query(filters.regex("^exec_stream_remove$"))
async def exec_rm_cb(client: Client, callback_query: CallbackQuery):
    await execute_processing(client, callback_query.from_user.id, callback_query.message)

@Client.on_callback_query(filters.regex("^exec_extract_"))
async def exec_single_extract(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)
    
    stream_idx = int(callback_query.data.split("_")[-1])
    state = USER_STATES[user_id]
    task_id = state["task_id"].split("_")[-1]
    status_msg = callback_query.message

    try:
        await status_msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    input_path = state["local_path"]
    streams = state.get("available_streams", [])
    
    user_custom_name = state.get("new_name", state.get("file_name", "extracted_stream"))
    base_name, _ = os.path.splitext(user_custom_name)

    ext = ".mkv"
    if stream_idx < len(streams):
        stype = streams[stream_idx].get("codec_type")
        codec = streams[stream_idx].get("codec_name", "").lower()
        if stype == "audio":
            ext = ".mp3" if codec == "mp3" else ".mka"
        elif stype in ["subtitle", "subrip"]:
            ext = ".srt" if codec in ["srt", "subrip"] else (".ass" if codec in ["ass", "ssa"] else ".mks")
        elif stype == "video":
            ext = ".mp4" if codec == "h264" else ".mkv"

    final_filename = f"{base_name}_Stream_{stream_idx}{ext}"
    ext_out = os.path.join(Config.DOWNLOAD_DIR, final_filename)

    start_time = time.time()
    await progress_bar(0, 100, f"Extracting Stream {stream_idx}", status_msg, start_time, task_id, callback_query.from_user, force_update=True)

    success = await extract_stream(input_path, ext_out, stream_idx)
    if success and os.path.exists(ext_out):
        send_size = os.path.getsize(ext_out)
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=ext_out,
            file_name=final_filename,
            caption=f"<b>✅ Extracted File:</b> <code>{final_filename}</code>",
            progress=progress_bar,
            progress_args=("Uploading Extracted Stream", status_msg, time.time(), task_id, callback_query.from_user)
        )

    if os.path.exists(ext_out): os.remove(ext_out)
    if os.path.exists(input_path): os.remove(input_path)
    await status_msg.delete()
    USER_STATES.pop(user_id, None)

async def execute_processing(client: Client, user_id: int, message: Message):
    if user_id not in USER_STATES:
        return
    state = USER_STATES[user_id]
    
    raw_task_id = state["task_id"]
    task_id = raw_task_id.split("_")[-1]
    new_name = state["new_name"]
    file_msg = state["message"]
    
    status_msg = message
    try:
        await status_msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    local_path = state.get("local_path")
    file_size = getattr(state["message"].video or state["message"].document, "file_size", 0)
    
    if not local_path or not os.path.exists(local_path):
        await progress_bar(0, file_size, "Downloading", status_msg, time.time(), task_id, message.from_user, force_update=True)
        local_path = os.path.join(Config.DOWNLOAD_DIR, f"{raw_task_id}_{state['file_name']}")
        await client.download_media(
            message=file_msg, file_name=local_path, progress=progress_bar,
            progress_args=("Downloading", status_msg, time.time(), task_id, message.from_user)
        )

    output_path = os.path.join(Config.DOWNLOAD_DIR, new_name)
    actions = state.get("selected_actions", {})

    await progress_bar(0, file_size, "Processing", status_msg, time.time(), task_id, message.from_user, force_update=True)
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

    thumb_path = os.path.join(Config.DOWNLOAD_DIR, f"thumb_{user_id}.jpg")
    custom_thumb = thumb_path if os.path.exists(thumb_path) else None

    for file_to_send, media_type in outputs_to_upload:
        if raw_task_id in STOPPED_TASKS or task_id in STOPPED_TASKS:
            await status_msg.edit_text("❌ Task Cancelled by user.")
            break

        start_time = time.time()
        send_size = os.path.getsize(file_to_send) if os.path.exists(file_to_send) else file_size
        await progress_bar(0, send_size, "Uploading", status_msg, start_time, task_id, message.from_user, force_update=True)

        if media_type == "video":
            await client.send_video(
                chat_id=message.chat.id,
                video=file_to_send,
                thumb=custom_thumb,
                caption=f"<b>📄 File Name:</b> <code>{os.path.basename(file_to_send)}</code>",
                progress=progress_bar,
                progress_args=("Uploading", status_msg, start_time, task_id, message.from_user)
            )
        elif media_type == "audio":
            await client.send_audio(chat_id=message.chat.id, audio=file_to_send, caption=f"<b>🎵 Extracted Audio:</b> <code>{os.path.basename(file_to_send)}</code>")
        elif media_type == "photo":
            await client.send_photo(chat_id=message.chat.id, photo=file_to_send, caption=f"<b>📸 Captured Screenshot</b>")

        if os.path.exists(file_to_send): os.remove(file_to_send)

    await status_msg.delete()
    USER_STATES.pop(user_id, None)
