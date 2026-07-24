"""
Async MongoDB layer for the rename bot, built on Motor.
Handles user settings, stats, and ban state.
"""

import datetime
import motor.motor_asyncio

import config


class Database:
    def __init__(self, uri: str, database_name: str):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.users = self.db.users

    # ---------------------------------------------------------------- #
    # User lifecycle
    # ---------------------------------------------------------------- #
    def new_user(self, user_id: int, first_name: str, last_name: str, username: str) -> dict:
        return {
            "user_id": user_id,
            "first_name": first_name or "",
            "last_name": last_name or "",
            "username": username or "",
            "thumbnail": None,
            "metadata_title": None,
            "metadata_artist": None,
            "metadata_album": None,
            "metadata_year": None,
            "caption": None,
            "prefix": None,
            "suffix": None,
            "banned": False,
            "joined_date": datetime.datetime.utcnow(),
            "last_active": datetime.datetime.utcnow(),
            "total_processed": 0,
        }

    async def add_user_if_new(self, user_id: int, first_name: str, last_name: str, username: str):
        existing = await self.users.find_one({"user_id": user_id})
        if existing is None:
            await self.users.insert_one(self.new_user(user_id, first_name, last_name, username))
            return True
        await self.users.update_one(
            {"user_id": user_id}, {"$set": {"last_active": datetime.datetime.utcnow()}}
        )
        return False

    async def get_user(self, user_id: int) -> dict:
        user = await self.users.find_one({"user_id": user_id})
        if not user:
            user = self.new_user(user_id, "", "", "")
            await self.users.insert_one(user)
        return user

    async def total_users_count(self) -> int:
        return await self.users.count_documents({})

    async def active_today_count(self) -> int:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        return await self.users.count_documents({"last_active": {"$gte": since}})

    async def get_all_user_ids(self):
        cursor = self.users.find({}, {"user_id": 1})
        return [doc["user_id"] async for doc in cursor]

    # ---------------------------------------------------------------- #
    # Ban management
    # ---------------------------------------------------------------- #
    async def ban_user(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"banned": True}}, upsert=True)

    async def unban_user(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"banned": False}}, upsert=True)

    async def is_banned(self, user_id: int) -> bool:
        user = await self.users.find_one({"user_id": user_id})
        return bool(user and user.get("banned"))

    async def banned_count(self) -> int:
        return await self.users.count_documents({"banned": True})

    # ---------------------------------------------------------------- #
    # Settings: thumbnail / metadata / caption / prefix / suffix
    # ---------------------------------------------------------------- #
    async def set_thumbnail(self, user_id: int, file_id: str):
        await self.users.update_one({"user_id": user_id}, {"$set": {"thumbnail": file_id}}, upsert=True)

    async def del_thumbnail(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"thumbnail": None}})

    async def get_thumbnail(self, user_id: int):
        user = await self.get_user(user_id)
        return user.get("thumbnail")

    async def set_metadata(self, user_id: int, title: str, artist: str, album: str, year: str):
        await self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "metadata_title": title,
                    "metadata_artist": artist,
                    "metadata_album": album,
                    "metadata_year": year,
                }
            },
            upsert=True,
        )

    async def del_metadata(self, user_id: int):
        await self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "metadata_title": None,
                    "metadata_artist": None,
                    "metadata_album": None,
                    "metadata_year": None,
                }
            },
        )

    async def set_caption(self, user_id: int, caption: str):
        await self.users.update_one({"user_id": user_id}, {"$set": {"caption": caption}}, upsert=True)

    async def del_caption(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"caption": None}})

    async def set_prefix(self, user_id: int, prefix: str):
        await self.users.update_one({"user_id": user_id}, {"$set": {"prefix": prefix}}, upsert=True)

    async def del_prefix(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"prefix": None}})

    async def set_suffix(self, user_id: int, suffix: str):
        await self.users.update_one({"user_id": user_id}, {"$set": {"suffix": suffix}}, upsert=True)

    async def del_suffix(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"suffix": None}})

    # ---------------------------------------------------------------- #
    # Stats
    # ---------------------------------------------------------------- #
    async def increment_processed(self, user_id: int):
        await self.users.update_one(
            {"user_id": user_id},
            {"$inc": {"total_processed": 1}, "$set": {"last_active": datetime.datetime.utcnow()}},
            upsert=True,
        )


db = Database(config.MONGO_URL, config.DB_NAME)
