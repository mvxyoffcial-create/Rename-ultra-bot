import asyncio
import json
import logging
import re
import os
import time

logger = logging.getLogger(__name__)

async def get_media_duration(file_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", file_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    return float(data.get("format", {}).get("duration", 0))

async def run_ffmpeg_with_progress(cmd: list, input_path: str, output_path: str, progress_callback, status_message, task_id, user):
    total_duration = await get_media_duration(input_path)
    total_bytes = os.path.getsize(input_path) if os.path.exists(input_path) else 1
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    start_time = time.time()
    time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

    while True:
        line = await proc.stderr.readline()
        if not line:
            break
            
        line_str = line.decode('utf-8', errors='ignore')
        match = time_regex.search(line_str)
        if match and total_duration > 0:
            hours, minutes, seconds = map(float, match.groups())
            current_time = hours * 3600 + minutes * 60 + seconds
            current_bytes = int((current_time / total_duration) * total_bytes)
            
            await progress_callback(
                current=min(current_bytes, total_bytes),
                total=total_bytes,
                status_type="Processing",
                message=status_message,
                start_time=start_time,
                task_id=task_id,
                user=user
            )

    await proc.wait()
    return proc.returncode == 0

async def get_media_streams(file_path: str):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    return data.get("streams", [])

async def remove_streams_with_progress(input_path: str, output_path: str, streams_to_remove: list, progress_callback, status_message, task_id, user):
    streams = await get_media_streams(input_path)
    cmd = ["ffmpeg", "-y", "-i", input_path]
    
    for i, _ in enumerate(streams):
        if i not in streams_to_remove:
            cmd.extend(["-map", f"0:{i}"])
            
    cmd.extend(["-c", "copy", output_path])
    return await run_ffmpeg_with_progress(cmd, input_path, output_path, progress_callback, status_message, task_id, user)

async def extract_audio_with_progress(input_path: str, output_path: str, progress_callback, status_message, task_id, user):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path]
    return await run_ffmpeg_with_progress(cmd, input_path, output_path, progress_callback, status_message, task_id, user)

async def extract_stream(input_path: str, output_path: str, stream_index: int):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", f"0:{stream_index}", "-c", "copy", output_path]
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
