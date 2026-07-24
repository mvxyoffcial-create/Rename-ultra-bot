import os
import time
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from handlers.rename import USER_STATES
from ffmpeg_tools import get_media_streams, remove_streams, extract_audio, take_screenshot, create_sample, extract_stream
from utils import progress_bar
from config import Config

@Client.on_callback_query(filters.regex("tool_action_done"))
async def on_done_click(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)

    state = USER_STATES[user_id]
    actions = state["selected_actions"]

    # 1. Handle Stream Extract Workflow
    if actions.get("extract"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video streams for extraction...")
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
        text = "<b>📤 Stream Extract Menu:</b>\n\nSelect a stream to extract:\n"
        for idx, s in enumerate(streams):
            codec = s.get("codec_name", "unknown")
            stype = s.get("codec_type", "unknown").capitalize()
            lang = s.get("tags", {}).get("language", "und")
            text += f"• <b>Stream {idx}:</b> {stype} ({codec}) [{lang}]\n"
            buttons.append([InlineKeyboardButton(f"Extract Stream {idx} ({stype})", callback_data=f"exec_extract_{idx}")])
        
        buttons.append([InlineKeyboardButton("Close ❌", callback_data="tool_action_close")])
        return await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    # 2. Handle Stream Remove Workflow
    if actions.get("remove"):
        status_msg = await callback_query.message.edit_text("🔍 Analyzing video streams for removal...")
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
        text = "<b>🗑️ Stream Remove Menu:</b>\n\nSelect streams to remove:\n"
        for idx, s in enumerate(streams):
            codec = s.get("codec_name", "unknown")
            stype = s.get("codec_type", "unknown").capitalize()
            lang = s.get("tags", {}).get("language", "und")
            text += f"• <b>Stream {idx}:</b> {stype} ({codec}) [{lang}]\n"
            buttons.append([InlineKeyboardButton(f"☐ Stream {idx} ({stype})", callback_data=f"select_rm_{idx}")])
        
        buttons.append([InlineKeyboardButton("✅ Confirm & Execute", callback_data="exec_stream_remove")])
        buttons.append([InlineKeyboardButton("Close ❌", callback_data="tool_action_close")])
        state["remove_selected"] = []
        return await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    await execute_processing(client, user_id, callback_query.message)

@Client.on_callback_query(filters.regex("^exec_extract_"))
async def exec_single_extract(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)
    
    stream_idx = int(callback_query.data.split("_")[-1])
    state = USER_STATES[user_id]
    status_msg = await callback_query.message.edit_text(f"⚙️ Extracting Stream {stream_idx}...")
    
    input_path = state["local_path"]
    ext_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(state['new_name'])[0]}_stream_{stream_idx}.mkv")
    
    success = await extract_stream(input_path, ext_out, stream_idx)
    if success:
        start_time = time.time()
        await client.send_document(
            chat_id=callback_query.message.chat.id,
            document=ext_out,
            caption=f"<b>✅ Stream {stream_idx} Extracted Successfully!</b>",
            progress=progress_bar,
            progress_args=("📤 Uploading Stream", status_msg, start_time, state["task_id"], callback_query.from_user)
        )
    
    if os.path.exists(ext_out):
        os.remove(ext_out)
    if os.path.exists(input_path):
        os.remove(input_path)

    await status_msg.delete()
    USER_STATES.pop(user_id, None)

@Client.on_callback_query(filters.regex("^select_rm_"))
async def select_stream_rm(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return
    idx = int(callback_query.data.split("_")[-1])
    sel = USER_STATES[user_id].get("remove_selected", [])
    
    if idx in sel:
        sel.remove(idx)
    else:
        sel.append(idx)
    USER_STATES[user_id]["remove_selected"] = sel
    await callback_query.answer(f"Selected streams to remove: {sel}")

@Client.on_callback_query(filters.regex("exec_stream_remove"))
async def exec_rm_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await execute_processing(client, user_id, callback_query.message)

async def execute_processing(client: Client, user_id: int, message: Message):
    if user_id not in USER_STATES:
        return
    state = USER_STATES[user_id]
    status_msg = await message.reply_text("⚡ Starting Task Execution...")
    
    task_id = state["task_id"]
    new_name = state["new_name"]
    file_msg = state["message"]
    
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    local_path = state.get("local_path")
    
    if not local_path or not os.path.exists(local_path):
        start_time = time.time()
        local_path = os.path.join(Config.DOWNLOAD_DIR, f"{task_id}_{state['file_name']}")
        await client.download_media(
            message=file_msg,
            file_name=local_path,
            progress=progress_bar,
            progress_args=("📥 Download", status_msg, start_time, task_id, message.from_user)
        )

    output_path = os.path.join(Config.DOWNLOAD_DIR, new_name)
    actions = state.get("selected_actions", {})

    await status_msg.edit_text("⚙️ Processing media with FFmpeg engine...")
    if actions.get("remove") and state.get("remove_selected"):
        await remove_streams(local_path, output_path, state["remove_selected"])
    else:
        os.rename(local_path, output_path)

    outputs_to_upload = [(output_path, "video")]

    if actions.get("audio"):
        audio_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_audio.mp3")
        if await extract_audio(output_path, audio_out):
            outputs_to_upload.append((audio_out, "audio"))

    if actions.get("screenshot"):
        ss_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_screenshot.jpg")
        if await take_screenshot(output_path, ss_out):
            outputs_to_upload.append((ss_out, "photo"))

    if actions.get("sample"):
        sample_out = os.path.join(Config.DOWNLOAD_DIR, f"{os.path.splitext(new_name)[0]}_sample.mp4")
        if await create_sample(output_path, sample_out):
            outputs_to_upload.append((sample_out, "video"))

    for file_to_send, media_type in outputs_to_upload:
        start_time = time.time()
        if media_type == "video":
            await client.send_video(
                chat_id=message.chat.id,
                video=file_to_send,
                caption=f"<b>✅ Task Completed!</b>\n📄 <b>File:</b> <code>{os.path.basename(file_to_send)}</code>",
                progress=progress_bar,
                progress_args=("📤 Uploading", status_msg, start_time, task_id, message.from_user)
            )
        elif media_type == "audio":
            await client.send_audio(chat_id=message.chat.id, audio=file_to_send)
        elif media_type == "photo":
            await client.send_photo(chat_id=message.chat.id, photo=file_to_send)

        if os.path.exists(file_to_send):
            os.remove(file_to_send)

    # Automatically clean status and tool messages
    await status_msg.delete()
    if "menu_message_id" in state:
        try:
            await client.delete_messages(chat_id=message.chat.id, message_ids=state["menu_message_id"])
        except Exception:
            pass

    USER_STATES.pop(user_id, None)
