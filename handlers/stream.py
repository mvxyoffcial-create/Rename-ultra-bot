import os
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

# Import shared state from rename handler
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
    return lang_map.get(str(lang_code).lower(), str(lang_code).upper())

def build_remove_menu_markup(streams: list, selected_indices: list):
    buttons = []
    
    # 1. Stream toggle buttons
    for idx, s in enumerate(streams):
        codec = s.get("codec_name", "unknown")
        stype = s.get("codec_type", "unknown").capitalize()
        lang = format_language(s.get("tags", {}).get("language", "und"))
        
        mark = "✅" if idx in selected_indices else "⬜"
        btn_text = f"{mark} Stream {idx}: {stype} ({codec}) [{lang}]"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"select_rm_{idx}")])

    # 2. Bulk quick-selection buttons
    buttons.append([
        InlineKeyboardButton("🎵 All Audio", callback_data="select_rm_all_audio"),
        InlineKeyboardButton("📝 All Subtitles", callback_data="select_rm_all_subs")
    ])
    
    # 3. Action controls
    buttons.append([
        InlineKeyboardButton("✅ Confirm & Execute", callback_data="exec_stream_remove"),
        InlineKeyboardButton("Close ❌", callback_data="tool_action_close")
    ])
    
    return InlineKeyboardMarkup(buttons)

# Command to stop/cancel running task
@Client.on_message(filters.regex(r"^/stop_"))
async def stop_task_handler(client: Client, message: Message):
    task_id = message.text.replace("/stop_", "").strip()
    STOPPED_TASKS.add(task_id)
    await message.reply_text(f"🛑 <b>Task {task_id} cancellation requested.</b>")

# Callback to refresh progress status
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
        await callback_query.answer("No active status found.", show_alert=True)

# Main handler triggered after clicking "✅ Done" in tool menu
@Client.on_callback_query(filters.regex("^tool_action_done$"))
async def on_done_click(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Session expired! Please resend file.", show_alert=True)

    state = USER_STATES[user_id]
    actions = state.get("selected_actions", {})

    if not any(actions.values()):
        return await callback_query.answer("⚠️ Please select at least one action!", show_alert=True)

    # 1. Stream Extract Flow
    if actions.get("extract"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video & audio streams...")
        file_msg = state["message"]
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        temp_path = os.path.join(Config.DOWNLOAD_DIR, f"temp_{user_id}_{state['file_name']}")
        
        start_time = time.time()
        dl_path = await client.download_media(
            message=file_msg,
            file_name=temp_path,
            progress=progress_bar,
            progress_args=("Downloading for Analysis", status_msg, start_time, state["task_id"], callback_query.from_user)
        )
        state["local_path"] = dl_path
        streams = await get_media_streams(dl_path)
        
        buttons = []
        text = "<b>📤 Stream Extract Menu:</b>\n\nSelect a stream to extract:\n\n"
        for idx, s in enumerate(streams):
            codec = s.get("codec_name", "unknown")
            stype = s.get("codec_type", "unknown").capitalize()
            lang = format_language(s.get("tags", {}).get("language", "und"))
            text += f"• <b>Stream {idx}:</b> {stype} ({codec}) - 🌐 <b>{lang}</b>\n"
            buttons.append([InlineKeyboardButton(f"Extract Stream {idx} ({stype} - {lang})", callback_data=f"exec_extract_{idx}")])
        
        buttons.append([InlineKeyboardButton("Close ❌", callback_data="tool_action_close")])
        return await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    # 2. Stream Remove Flow (Interactive Menu)
    if actions.get("remove"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video & audio streams...")
        file_msg = state["message"]
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        temp_path = os.path.join(Config.DOWNLOAD_DIR, f"temp_{user_id}_{state['file_name']}")
        
        start_time = time.time()
        dl_path = await client.download_media(
            message=file_msg,
            file_name=temp_path,
            progress=progress_bar,
            progress_args=("Downloading for Analysis", status_msg, start_time, state["task_id"], callback_query.from_user)
        )
        state["local_path"] = dl_path
        streams = await get_media_streams(dl_path)
        state["available_streams"] = streams
        state["remove_selected"] = []

        text = "<b>🗑️ Stream Remover Menu:</b>\n\nSelect streams to remove from the video:"
        return await status_msg.edit_text(text, reply_markup=build_remove_menu_markup(streams, []))

    # 3. Direct Execution for standard processing actions
    await execute_processing(client, user_id, callback_query.message)

# Handlers for toggling options in the Stream Remover checklist
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
        if all(idx in sel for idx in audio_indices):
            sel = [idx for idx in sel if idx not in audio_indices]
            await callback_query.answer("Deselected all audio streams.")
        else:
            sel = list(set(sel + audio_indices))
            await callback_query.answer("Selected all audio streams.")

    elif action == "all_subs":
        sub_indices = [idx for idx, s in enumerate(streams) if s.get("codec_type") in ["subtitle", "subrip"]]
        if all(idx in sel for idx in sub_indices):
            sel = [idx for idx in sel if idx not in sub_indices]
            await callback_query.answer("Deselected all subtitle streams.")
        else:
            sel = list(set(sel + sub_indices))
            await callback_query.answer("Selected all subtitle streams.")

    else:
        idx = int(action)
        if idx in sel:
            sel.remove(idx)
            await callback_query.answer(f"Removed Stream {idx}")
        else:
            sel.append(idx)
            await callback_query.answer(f"Selected Stream {idx}")

    state["remove_selected"] = sel
    await callback_query.message.edit_reply_markup(reply_markup=build_remove_menu_markup(streams, sel))

@Client.on_callback_query(filters.regex("^exec_stream_remove$"))
async def exec_rm_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await execute_processing(client, user_id, callback_query.message)

@Client.on_callback_query(filters.regex("^exec_extract_"))
async def exec_single_extract(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)
    
    stream_idx = int(callback_query.data.split("_")[-1])
    state = USER_STATES[user_id]
    task_id = state["task_id"].split("_")[-1]
    
    status_msg = await callback_query.message.edit_text("Starting stream extraction...")
    input_path = state["local_path"]
    ext_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(state['new_name'])[0]}_stream_{stream_idx}.mkv")
    
    success = await extract_stream(input_path, ext_out, stream_idx)
    if success:
        start_time = time.time()
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=ext_out,
            caption=f"<b>✅ Extracted Stream {stream_idx} File!</b>",
            progress=progress_bar,
            progress_args=("Uploading Stream", status_msg, start_time, task_id, callback_query.from_user)
        )
    
    if os.path.exists(ext_out):
        os.remove(ext_out)
    if os.path.exists(input_path):
        os.remove(input_path)

    await status_msg.delete()
    USER_STATES.pop(user_id, None)

# Main Execution Engine
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
    
    start_time = time.time()
    file_size = getattr(state["message"].video or state["message"].document, "file_size", 0)
    
    # 1. Download Stage
    await progress_bar(0, file_size, "Downloading", status_msg, start_time, task_id, message.from_user, force_update=True)
    if not local_path or not os.path.exists(local_path):
        local_path = os.path.join(Config.DOWNLOAD_DIR, f"{raw_task_id}_{state['file_name']}")
        await client.download_media(
            message=file_msg,
            file_name=local_path,
            progress=progress_bar,
            progress_args=("Downloading", status_msg, start_time, task_id, message.from_user)
        )

    output_path = os.path.join(Config.DOWNLOAD_DIR, new_name)
    actions = state.get("selected_actions", {})

    # 2. FFmpeg Processing Stage
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

    # 3. Upload Stage
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
                caption=f"<b>📄 File Name:</b> <code>{os.path.basename(file_to_send)}</code>",
                progress=progress_bar,
                progress_args=("Uploading", status_msg, start_time, task_id, message.from_user)
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
