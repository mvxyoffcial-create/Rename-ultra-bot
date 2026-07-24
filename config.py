"""
Rename Bot 4GB Ultra - Configuration
"""

# ============ TELEGRAM API ============
API_ID = 36282056
API_HASH = "3a948acece533f362b4c90b2b3c14b60"
BOT_TOKEN = "8701355562:AAFJxO2AQPfmMTmKMo7V2jyq2drX7kb9JRo"  # Put your new token from @BotFather

# Optional userbot session string (for files > 2GB via Telegram Premium account)
SESSION_STRING = ""

# ============ DEVELOPER ============
DEVELOPER = "@Spidey2189"
OWNER = "@Spidey2189"

# ============ MONGODB ============
MONGO_URI = "mongodb+srv://filmzi2120_db_user:zero8907@cluster0.zyau0re.mongodb.net/?appName=Cluster0"
DB_NAME = "rename_bot_ultra"

# ============ FORCE SUBSCRIBE ============
# NOTE: Not enforced anywhere in bot.py by design (disabled per request).
# Listed here only so the field exists if you decide to wire it up later.
FORCE_SUB_CHANNELS = [
    "spideyoffcail",
    "mvxyoffcail",
]

# ============ WELCOME ============
WELCOME_STICKER = "CAACAgIAAxkBAAEQZtFpgEdROhGouBVFD3e0K-YjmVHwsgACtCMAAphLKUjeub7NKlvk2TgE"
WELCOME_STICKER_DELETE_AFTER = 2  # seconds

# ============ ADMINS ============
ADMIN_IDS = [8498741978]

# ============ BOT BEHAVIOUR ============
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
DOWNLOAD_DIR = "./downloads"

# How many ffmpeg jobs can run at the same time. Real hardware can't do
# anything close to "500 concurrent" video transcodes -- this controls the
# actual parallel processing limit. Raise it if you have the CPU/RAM for it.
MAX_CONCURRENT_FFMPEG = 3

# How many simultaneous download/upload transfers to allow.
MAX_CONCURRENT_TRANSFERS = 10

# Pyrogram worker pool size (thread pool used internally by Pyrogram for
# handling updates).
PYROGRAM_WORKERS = 500

# How often (seconds) to edit the progress message during transfers.
PROGRESS_UPDATE_INTERVAL = 2

SUPPORTED_VIDEO_FORMATS = ["mp4", "mkv", "avi", "mov", "webm", "ts"]
SUPPORTED_AUDIO_FORMATS = ["mp3", "aac", "m4a", "flac", "opus"]
