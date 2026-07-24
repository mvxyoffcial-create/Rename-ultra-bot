import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.environ.get("API_ID", "123456"))
    API_HASH = os.environ.get("API_HASH", "your_api_hash")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
    
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "videobot_db")
    
    OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
    FORCE_SUB_CHANNELS = os.environ.get("FORCE_SUB_CHANNELS", "spideyoffcail,mvxyoffcail").split(",")
    
    PORT = int(os.environ.get("PORT", "8000"))
    WORKERS = int(os.environ.get("WORKERS", "500"))
    
    DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloads")
    
    STICKER_ID = "CAACAgIAAxkBAAEQZtFpgEdROhGouBVFD3e0K-YjmVHwsgACtCMAAphLKUjeub7NKlvk2TgE"
    PICS_URL = ["https://api.aniwallpaper.workers.dev/random?type=girl"]
