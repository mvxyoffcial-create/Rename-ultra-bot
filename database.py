import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

client = AsyncIOMotorClient(Config.MONGO_URI)
db = client[Config.DATABASE_NAME]

users_col = db["users"]
tasks_col = db["tasks"]
files_col = db["files"]

async def get_user(user_id: int):
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "first_name": "",
            "last_name": "",
            "username": "",
            "is_premium": False,
            "premium_until": None,
            "total_files": 0,
            "joined_date": datetime.datetime.now(datetime.timezone.utc),
            "is_banned": False,
            "settings": {
                "video_tools": True,
                "auto_rename": False,
                "progress_detailed": True,
                "language": "en"
            },
            "thumbnail": None,
            "used_today": 0,
            "last_used": datetime.datetime.now(datetime.timezone.utc)
        }
        await users_col.insert_one(user)
    return user

async def update_user_settings(user_id: int, key: str, value):
    await users_col.update_one({"user_id": user_id}, {"$set": {f"settings.{key}": value}})

async def set_user_thumbnail(user_id: int, file_id: str):
    await users_col.update_one({"user_id": user_id}, {"$set": {"thumbnail": file_id}})

async def create_task(task_data: dict):
    await tasks_col.insert_one(task_data)

async def update_task(task_id: str, data: dict):
    await tasks_col.update_one({"task_id": task_id}, {"$set": data})
