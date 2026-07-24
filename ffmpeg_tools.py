import asyncio
import json
import logging

logger = logging.getLogger(__name__)

async def get_media_streams(file_path: str):
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        file_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    return data.get("streams", [])

async def remove_streams(input_path: str, output_path: str, streams_to_remove: list):
    # Construct map parameters to exclude selected stream indices
    streams = await get_media_streams(input_path)
    cmd = ["ffmpeg", "-y", "-i", input_path]
    
    for i, _ in enumerate(streams):
        if i not in streams_to_remove:
            cmd.extend(["-map", f"0:{i}"])
            
    cmd.extend(["-c", "copy", output_path])
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0

async def extract_stream(input_path: str, output_path: str, stream_index: int):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", f"0:{stream_index}", "-c", "copy", output_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0

async def extract_audio(input_path: str, output_path: str):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0

async def extract_subtitle(input_path: str, output_path: str, stream_index: int):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", f"0:{stream_index}", output_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0

async def take_screenshot(input_path: str, output_path: str, timestamp: str = "00:00:05"):
    cmd = ["ffmpeg", "-y", "-ss", timestamp, "-i", input_path, "-vframes", "1", "-q:v", "2", output_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0

async def create_sample(input_path: str, output_path: str, duration: int = 30):
    cmd = ["ffmpeg", "-y", "-ss", "00:00:10", "-i", input_path, "-t", str(duration), "-c", "copy", output_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0
