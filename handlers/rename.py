import os
import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database import get_user, create_task
from utils import get_random_mix_id

# Temporary state storage for file workflows
USER_STATES = {}

def build_tools_markup(selected_actions: dict):
    def mark(key):
        return "✅" if selected_actions.get(key) else "⬜"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video Processing", callback_data="ignore")],
        [InlineKeyboardButton(f"{mark('remove')} Stream Remove 🗑️", callback_data="tool_toggle_remove")],
        [InlineKeyboardButton(f"{mark('extract')} Stream Extract 📤", callback_data="tool_toggle_extract")],
        [InlineKeyboardButton(f"{mark('audio')} Extract Audio 🎵", callback_data="tool_toggle_audio")],
        [InlineKeyboardButton(f"{mark('subtitle')} Extract Subtitle 📝", callback_data="tool_toggle_subtitle")],
        [InlineKeyboardButton(f"{mark('screenshot')} Take Screenshot 📸", callback_data="tool_toggle_screenshot")],
        [InlineKeyboardButton(f"{mark('sample')} Sample Video 🎬", callback_data="tool_toggle_sample")],
        [InlineKeyboardButton("✅ Done", callback_data="tool_action_done")]
    ])

@Client.on_message((filters.video | filters.document) & filters.private)
async def handle_media(client: Client, message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    file = message.video or message.document
    if not file:
        return

    # Check 2GB limits for standard users
    if file.file_size > 2 * 1024 * 1024 * 1024 and not user.get("is_premium"):
        return await message.reply_text("<b>❌ File Size Limit Exceeded!</b>\nFree users can only process files up to 2GB. Upgrade to Premium for 4GB support.")

    USER_STATES[user_id] = {
        "message": message,
        "file_name": getattr(file, "file_name", "video.mp4") or "video.mp4",
        "file_size": file.file_size,
        "selected_actions": {},
        "task_id": f"task_{get_random_mix_id()}"
    }

    await message.reply_text(
        f"<b>📁 File Received:</b> <code>{USER_STATES[user_id]['file_name']}</code>\n\n"
        f"Please send the <b>new name</b> for this file (including extension, e.g. <code>New_Movie.mp4</code>):",
        reply_to_message_id=message.id
    )

@Client.on_message(filters.text & filters.private & ~filters.command(["start", "settings", "help", "info"]))
async def handle_new_name(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in USER_STATES or "new_name" in USER_STATES[user_id]:
        return

    new_name = message.text.strip()
    USER_STATES[user_id]["new_name"] = new_name
    
    user = await get_user(user_id)
    if user["settings"]["video_tools"]:
        await message.reply_text(
            f"<b>✏️ New Name Set:</b> <code>{new_name}</code>\n\nSelect the actions you wish to execute:",
            reply_markup=build_tools_markup(USER_STATES[user_id]["selected_actions"])
        )
    else:
        # Direct execution without tools menu
        from handlers.stream import execute_processing
        await execute_processing(client, user_id, message)

@Client.on_callback_query(filters.regex("^tool_toggle_"))
async def toggle_tools_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("Task expired!", show_alert=True)

    action = callback_query.data.replace("tool_toggle_", "")
    curr = USER_STATES[user_id]["selected_actions"].get(action, False)
    USER_STATES[user_id]["selected_actions"][action] = not curr

    await callback_query.message.edit_reply_markup(
        reply_markup=build_tools_markup(USER_STATES[user_id]["selected_actions"])
    )
    await callback_query.answer()
