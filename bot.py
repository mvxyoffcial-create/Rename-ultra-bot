import asyncio
import logging
from pyrogram import Client
from config import Config
from health import start_health_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

plugins = dict(root="handlers")

app = Client(
    name="VideoBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=Config.WORKERS,
    plugins=plugins
)

async def main():
    # Start internal health check HTTP web server
    await start_health_server()
    logging.info(f"Health server initialized on port {Config.PORT}")
    
    # Start Pyrogram Telegram Client
    await app.start()
    bot_info = await app.get_me()
    logging.info(f"Bot started successfully as @{bot_info.username}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
