from aiohttp import web
import psutil
from datetime import datetime
from config import Config

async def health_handler(request):
    data = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "workers": Config.WORKERS,
        "cpu_usage": psutil.cpu_percent(),
        "memory_usage": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "bot_running": True
    }
    return web.json_response(data)

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
