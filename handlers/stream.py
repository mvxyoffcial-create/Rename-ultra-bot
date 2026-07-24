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
from utils import progress_bar, format_language, PROGRESS_CACHE
from config import Config

STOPPED_TASKS = set()

# Helper to render the main tool menu keyboard dynamically
def build_tools_menu(actions: dict):
    def mark(key):
        return "✅" if actions.get(key) else "⬜"

    buttons = [
        [InlineKeyboardButton("🎬 Video Processing", callback_data="none")],
        [InlineKeyboardButton(f"{mark('remove')} Stream Remove 🗑️", callback_data="toggle_action_remove")],
        [InlineKeyboardButton(f"{mark('extract')} Stream Extract 📬", callback_data="toggle_action_extract")],
        [InlineKeyboardButton(f"{mark('audio')} Extract Audio 🎵", callback_data="toggle_action_audio")],
        [InlineKeyboardButton(f"{mark('subtitle')} Extract Subtitle 📝", callback_data="toggle_action_subtitle")],
        [InlineKeyboardButton(f"{mark('screenshot')} Take Screenshot 📸", callback_data="toggle_action_screenshot")],
        [InlineKeyboardButton(f"{mark('sample')} Sample Video 🎥", callback_data="toggle_action_sample")],
        [
            InlineKeyboardButton("✅ Done", callback_data="tool_action_done"),
            InlineKeyboardButton("Close ❌", callback_data="tool_action_close")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

# Helper to render the stream remover checklist keyboard
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

# 1. Toggle Tool Menu Options (Stream Remove, Stream Extract, etc.)
@Client.on_callback_query(filters.regex(r"^toggle_action_"))
async def toggle_tool_action(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Session expired! Please resend the file.", show_alert=True)

    action_key = callback_query.data.replace("toggle_action_", "")
    state = USER_STATES[user_id]
    
    if "selected_actions" not in state:
        state["selected_actions"] = {}

    # Toggle true/false
    current_val = state["selected_actions"].get(action_key, False)
    state["selected_actions"][action_key] = not current_val

    # Refresh menu with updated checkmarks
    await callback_query.message.edit_reply_markup(
        reply_markup=build_tools_menu(state["selected_actions"])
    )
    await callback_query.answer()

# 2. Handle "✅ Done" Click
@Client.on_callback_query(filters.regex("^tool_action_done$"))
async def on_done_click(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Session expired!", show_alert=True)

    state = USER_STATES[user_id]
    actions = state.get("selected_actions", {})

    if not any(actions.values()):
        return await callback_query.answer("⚠️ Please select at least one option!", show_alert=True)

    # If Stream Extract is chosen
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

    # If Stream Remove is chosen
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

        text = "<b>🗑️ Stream Remover Menu:</b>\n\nSelect streams to remove from the video below:"
        return await status_msg.edit_text(text, reply_markup=build_remove_menu_markup(streams, []))

    # General processing execution
    await execute_processing(client, user_id, callback_query.message)

# 3. Handle Close Click
@Client.on_callback_query(filters.regex("^tool_action_close$"))
async def on_close_click(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    USER_STATES.pop(user_id, None)
    await callback_query.message.delete()
    await callback_query.answer("Cancelled.")

# 4. Stream Remover Selection Checklist Callbacks
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
