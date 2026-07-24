import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config

# Global state dictionary shared across handlers
USER_STATES = {}
AWAITING_THUMB = set()

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

# Step 1: User sends a media file
@Client.on_message(filters.private & (filters.video | filters.document))
async def handle_incoming_file(client: Client, message: Message):
    user_id = message.from_user.id
    file = message.video or message.document
    file_name = file.file_name or "video.mp4"
    task_id = f"task_{user_id}_{int(message.date.timestamp() if message.date else 0)}"

    USER_STATES[user_id] = {
        "message": message,
        "file_name": file_name,
        "task_id": task_id,
        "selected_actions": {}
    }

    await message.reply_text(
        f"<b>📂 File Received:</b> <code>{file_name}</code>\n\n"
        "Please send the <b>new name</b> for this file (including extension, e.g., <code>MyVideo.mp4</code>):"
    )

# Step 2: User sends the new filename
@Client.on_message(filters.private & filters.text & ~filters.command(["start", "help", "settings"]))
async def process_new_name(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in USER_STATES or "message" not in USER_STATES[user_id]:
        return await message.reply_text("⚠️ No active file found. Please send or forward a media file first.")

    state = USER_STATES[user_id]
    state["new_name"] = message.text.strip()
    
    state["selected_actions"] = {
        "remove": False,
        "extract": False,
        "audio": False,
        "subtitle": False,
        "screenshot": False,
        "sample": False
    }

    menu_msg = await message.reply_text(
        f"<b>⚙️ Select Processing Tools for:</b>\n<code>{state['new_name']}</code>",
        reply_markup=build_tools_menu(state["selected_actions"])
    )
    state["menu_message_id"] = menu_msg.id

# Step 3: Toggle processing choices in menu
@Client.on_callback_query(filters.regex(r"^toggle_action_"))
async def toggle_action_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in USER_STATES:
        return await callback_query.answer("⚠️ Session expired! Resend file.", show_alert=True)

    action_key = callback_query.data.replace("toggle_action_", "")
    state = USER_STATES[user_id]
    
    current_status = state["selected_actions"].get(action_key, False)
    state["selected_actions"][action_key] = not current_status

    try:
        await callback_query.message.edit_reply_markup(
            reply_markup=build_tools_menu(state["selected_actions"])
        )
        await callback_query.answer()
    except Exception:
        await callback_query.answer()

# Settings Menu & Thumbnail Controls
@Client.on_callback_query(filters.regex("^open_settings$"))
async def open_settings_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    thumb_path = os.path.join(Config.DOWNLOAD_DIR, f"thumb_{user_id}.jpg")
    has_thumb = "✅ Saved" if os.path.exists(thumb_path) else "❌ Not Set"

    text = f"<b>⚙️ Bot Settings</b>\n\n<b>Custom Thumbnail:</b> {has_thumb}"
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb"),
            InlineKeyboardButton("🗑️ Delete Thumbnail", callback_data="delete_thumb")
        ],
        [
            InlineKeyboardButton("👁️ View Thumbnail", callback_data="view_thumb"),
            InlineKeyboardButton("Close ❌", callback_data="tool_action_close")
        ]
    ])
    await callback_query.message.edit_text(text, reply_markup=buttons)

@Client.on_callback_query(filters.regex("^set_thumb$"))
async def set_thumb_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    AWAITING_THUMB.add(user_id)
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel ❌", callback_data="open_settings")]])
    await callback_query.message.edit_text("📸 Please send or reply with the photo for thumbnail.", reply_markup=buttons)

@Client.on_message(filters.private & filters.photo)
async def save_photo_thumb(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in AWAITING_THUMB or message.reply_to_message:
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        thumb_path = os.path.join(Config.DOWNLOAD_DIR, f"thumb_{user_id}.jpg")
        await client.download_media(message=message.photo.file_id, file_name=thumb_path)
        AWAITING_THUMB.discard(user_id)
        await message.reply_text("✅ <b>Custom thumbnail saved successfully!</b>")

@Client.on_callback_query(filters.regex("^view_thumb$"))
async def view_thumb_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    thumb_path = os.path.join(Config.DOWNLOAD_DIR, f"thumb_{user_id}.jpg")
    if os.path.exists(thumb_path):
        await client.send_photo(chat_id=callback_query.message.chat.id, photo=thumb_path, caption="🖼️ Saved Thumbnail")
        await callback_query.answer()
    else:
        await callback_query.answer("⚠️ No custom thumbnail found!", show_alert=True)

@Client.on_callback_query(filters.regex("^delete_thumb$"))
async def delete_thumb_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    thumb_path = os.path.join(Config.DOWNLOAD_DIR, f"thumb_{user_id}.jpg")
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
        await callback_query.answer("🗑️ Thumbnail deleted!", show_alert=True)
        await open_settings_cb(client, callback_query)
    else:
        await callback_query.answer("⚠️ No thumbnail to delete.", show_alert=True)

@Client.on_callback_query(filters.regex("^tool_action_close$"))
async def close_menu_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    USER_STATES.pop(user_id, None)
    await callback_query.message.delete()
    await callback_query.answer("Cancelled.")
