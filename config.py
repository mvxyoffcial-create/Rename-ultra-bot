import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = 36282056
    API_HASH = "3a948acece533f362b4c90b2b3c14b60"
    BOT_TOKEN = "8285873350:AAHVx971B_3r-lJM804MkH288qqjMWHq_CI"
    
    MONGO_URI = "mongodb+srv://filmzi2120_db_user:zero8907@cluster0.zyau0re.mongodb.net/?appName=Cluster0"
    DATABASE_NAME = "videobot_db"
    
    OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
    FORCE_SUB_CHANNELS = "spideyoffcail,mvxyoffcail"
    
    PORT = "8000"
    WORKERS = "500"
    
    DOWNLOAD_DIR = "./downloads"
    
    STICKER_ID = "CAACAgIAAxkBAAEQZtFpgEdROhGouBVFD3e0K-YjmVHwsgACtCMAAphLKUjeub7NKlvk2TgE"
    PICS_URL = ["https://api.aniwallpaper.workers.dev/random?type=girl"]
