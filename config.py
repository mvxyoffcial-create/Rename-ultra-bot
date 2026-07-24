import os
from dotenv import load_dotenv

load_dotenv()

# ---------------- Telegram API ----------------
API_ID = int(os.getenv("API_ID", "12345678"))
API_HASH = os.getenv("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")

# ---------------- Database ----------------
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://username:password@cluster.mongodb.net")
DB_NAME = os.getenv("DB_NAME", "rename_bot")

# ---------------- Admins ----------------
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8498741978").split(",") if x.strip()]

# ---------------- Bot Behaviour ----------------
# Force-subscribe is intentionally NOT implemented in this build.
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")

# How many ffmpeg jobs can run at the same time. Real hardware can't do
# anything close to "500 concurrent" video transcodes -- this controls the
# actual parallel processing limit. Raise it if you have the CPU/RAM for it.
MAX_CONCURRENT_FFMPEG = int(os.getenv("MAX_CONCURRENT_FFMPEG", "3"))

# How many simultaneous download/upload transfers to allow.
MAX_CONCURRENT_TRANSFERS = int(os.getenv("MAX_CONCURRENT_TRANSFERS", "10"))

# Pyrogram worker pool size (thread pool used internally by Pyrogram for
# handling updates). 500 is what was requested; keep it configurable.
PYROGRAM_WORKERS = int(os.getenv("PYROGRAM_WORKERS", "500"))

# How often (seconds) to edit the progress message during transfers.
PROGRESS_UPDATE_INTERVAL = 2

BOT_START_TIME = None  # set at runtime in bot.py

SUPPORTED_VIDEO_FORMATS = ["mp4", "mkv", "avi", "mov", "webm", "ts"]
SUPPORTED_AUDIO_FORMATS = ["mp3", "aac", "m4a", "flac", "opus"]

DEVELOPER = "@Venuboyy"
OWNER = "@Venuboyy"
