import asyncio
import random
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user
from utils import check_force_sub, get_random_mix_id

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    await get_user(user_id)
    
    # Check force sub
    unsub_buttons = await check_force_sub(client, user_id)
    if unsub_buttons:
        unsub_buttons.append([InlineKeyboardButton("🔄 Refresh / Try Again", callback_data="check_subscription")])
        return await message.reply_text(
            "<b>⚠️ Access Denied!</b>\n\nPlease join our update channels to use this bot:",
            reply_markup=InlineKeyboardMarkup(unsub_buttons)
        )

    # Temporary sticker animation
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

@Client.on_callback_query(filters.regex("check_subscription"))
async def check_sub_cb(client, callback_query):
    unsub_buttons = await check_force_sub(client, callback_query.from_user.id)
    if unsub_buttons:
        await callback_query.answer("❌ You still haven't joined all channels!", show_alert=True)
    else:
        await callback_query.message.delete()
        await start_handler(client, callback_query.message)
