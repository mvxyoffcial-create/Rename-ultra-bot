import asyncio
import random
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import Config
from database import get_user
from utils import check_force_sub, get_random_mix_id

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    await get_user(user_id)
    
    unsub_buttons = await check_force_sub(client, user_id)
    if unsub_buttons:
        unsub_buttons.append([InlineKeyboardButton("🔄 Refresh / Try Again", callback_data="check_subscription")])
        return await message.reply_text(
            "<b>⚠️ Access Denied!</b>\n\nPlease join our update channels to use this bot:",
            reply_markup=InlineKeyboardMarkup(unsub_buttons)
        )

    try:
        stk = await message.reply_sticker(Config.STICKER_ID)
        await asyncio.sleep(2)
        await stk.delete()
    except Exception:
        pass

    welcome_img = f"{random.choice(Config.PICS_URL)}?r={get_random_mix_id()}"
    caption = (
        f"<b>ʜᴇʏ, {message.from_user.first_name}! 👋</b>\n\n"
        f"ɪ'ᴍ ᴀ <b>ᴠɪᴅᴇᴏ ᴘʀᴏᴄᴇssɪɴɢ ʙᴏᴛ</b> 🎬\n"
        f"ɪ ᴄᴀɴ ʀᴇɴᴀᴍᴇ, ᴘʀᴏᴄᴇss, ᴀɴᴅ ᴍᴀɴᴀɢᴇ ʏᴏᴜʀ ᴠɪᴅᴇᴏs! 📹\n\n"
        f"<b>📤 Sᴇɴᴅ ᴍᴇ ᴀ ᴠɪᴅᴇᴏ</b>\n"
        f"<b>✏️ Gɪᴠᴇ ɪᴛ ᴀ ɴᴇᴡ ɴᴀᴍᴇ</b>\n"
        f"<b>⚡ Pʀᴏᴄᴇss ᴡɪᴛʜ ᴀᴅᴠᴀɴᴄᴇᴅ ᴛᴏᴏʟs</b>\n\n"
        f"<b>🚀 Fᴇᴀᴛᴜʀᴇs:</b>\n"
        f"• Rᴇɴᴀᴍᴇ ғɪʟᴇs\n"
        f"• Rᴇᴍᴏᴠᴇ sᴛʀᴇᴀᴍs\n"
        f"• Exᴛʀᴀᴄᴛ ᴀᴜᴅɪᴏ/sᴜʙᴛɪᴛʟᴇs\n"
        f"• Tᴀᴋᴇ sᴄʀᴇᴇɴsʜᴏᴛs\n"
        f"• Cʀᴇᴀᴛᴇ sᴀᴍᴘʟᴇ ᴄʟɪᴘs\n\n"
        f"👨‍💻 Dᴇᴠᴇʟᴏᴘᴇʀ: @Venuboyy"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Settings", callback_data="open_settings"), InlineKeyboardButton("ℹ️ Help", callback_data="open_help")]
    ])
    
    await message.reply_photo(photo=welcome_img, caption=caption, reply_markup=buttons)

@Client.on_callback_query(filters.regex("open_settings"))
async def open_settings_cb(client: Client, callback_query: CallbackQuery):
    from handlers.settings import show_settings_menu
    await show_settings_menu(callback_query.from_user.id, callback_query)

@Client.on_callback_query(filters.regex("open_help"))
async def open_help_cb(client: Client, callback_query: CallbackQuery):
    help_text = (
        "<b>📖 How to Use Me:</b>\n\n"
        "1. Send any video file to the bot.\n"
        "2. Type and send the new filename.\n"
        "3. Choose actions (Remove stream, Extract stream, Extract Audio, Screenshots, etc.).\n"
        "4. Click <b>Done ✅</b> to start processing.\n\n"
        "Commands:\n"
        "• /settings - Customize your experience\n"
        "• /start - Restart bot"
    )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="back_to_start")]])
    await callback_query.message.edit_caption(caption=help_text, reply_markup=buttons)

@Client.on_callback_query(filters.regex("back_to_start"))
async def back_to_start_cb(client: Client, callback_query: CallbackQuery):
    await callback_query.message.delete()
    await start_handler(client, callback_query.message)

@Client.on_callback_query(filters.regex("check_subscription"))
async def check_sub_cb(client, callback_query):
    unsub_buttons = await check_force_sub(client, callback_query.from_user.id)
    if unsub_buttons:
        await callback_query.answer("❌ You still haven't joined all channels!", show_alert=True)
    else:
        await callback_query.message.delete()
        await start_handler(client, callback_query.message)
