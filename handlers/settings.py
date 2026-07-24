from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database import get_user, update_user_settings

@Client.on_message(filters.command("settings") & filters.private)
async def settings_command(client: Client, message: Message):
    await show_settings_menu(message.from_user.id, message)

async def show_settings_menu(user_id: int, target):
    user = await get_user(user_id)
    s = user["settings"]
    
    v_tools_status = "✅ ON" if s["video_tools"] else "❌ OFF"
    auto_rename_status = "✅ ON" if s["auto_rename"] else "❌ OFF"
    
    text = f"<b>⚙️ User Settings Panel</b>\n\nConfigure your bot preferences below:"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔧 Video Tools Menu: {v_tools_status}", callback_data="toggle_video_tools")],
        [InlineKeyboardButton(f"📁 Auto-Rename: {auto_rename_status}", callback_data="toggle_auto_rename")],
        [InlineKeyboardButton("🖼️ Set Custom Thumbnail", callback_data="set_thumb_info")],
        [InlineKeyboardButton("❌ Close", callback_data="close_settings")]
    ])
    
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=buttons)
    else:
        await target.reply_text(text, reply_markup=buttons)

@Client.on_callback_query(filters.regex("^toggle_"))
async def toggle_settings(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user = await get_user(user_id)
    
    if callback_query.data == "toggle_video_tools":
        new_val = not user["settings"]["video_tools"]
        await update_user_settings(user_id, "video_tools", new_val)
    elif callback_query.data == "toggle_auto_rename":
        new_val = not user["settings"]["auto_rename"]
        await update_user_settings(user_id, "auto_rename", new_val)
        
    await show_settings_menu(user_id, callback_query)

@Client.on_callback_query(filters.regex("close_settings"))
async def close_settings_cb(client, callback_query):
    await callback_query.message.delete()
