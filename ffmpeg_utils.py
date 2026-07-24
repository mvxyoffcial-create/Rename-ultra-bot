"""
ffmpeg-backed media operations: stream extract/remove, audio/subtitle
extract/remove, format conversion, screenshots, sample video, compression,
and metadata embedding.

All functions run ffmpeg as a subprocess via asyncio so they don't block
the event loop, and are guarded by a semaphore (config.MAX_CONCURRENT_FFMPEG)
so we don't fork-bomb the host.
"""

import asyncio
import os
import shlex

import config

ffmpeg_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_FFMPEG)


class FFmpegError(Exception):
    pass


async def _run(cmd: list):
    async with ffmpeg_semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise FFmpegError(stderr.decode(errors="ignore")[-2000:])
        return stdout, stderr


def _out_path(input_path: str, suffix: str, ext: str = None) -> str:
    base, orig_ext = os.path.splitext(input_path)
    ext = ext or orig_ext.lstrip(".")
    return f"{base}_{suffix}.{ext}"


async def stream_extract(input_path: str) -> str:
    """Keep video stream only."""
    out = _out_path(input_path, "video_only")
    await _run(["ffmpeg", "-y", "-i", input_path, "-map", "0:v", "-c", "copy", out])
    return out


async def stream_remove(input_path: str) -> str:
    """Keep audio stream only (drop video)."""
    out = _out_path(input_path, "audio_only", ext="mka")
    await _run(["ffmpeg", "-y", "-i", input_path, "-map", "0:a", "-c", "copy", out])
    return out


async def audio_extract(input_path: str, fmt: str = "mp3") -> str:
    codec_map = {
        "mp3": "libmp3lame",
        "aac": "aac",
        "m4a": "aac",
        "flac": "flac",
        "opus": "libopus",
    }
    codec = codec_map.get(fmt, "libmp3lame")
    out = _out_path(input_path, "audio", ext=fmt)
    await _run(["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", codec, out])
    return out


async def audio_remove(input_path: str) -> str:
    out = _out_path(input_path, "muted")
    await _run(["ffmpeg", "-y", "-i", input_path, "-an", "-c", "copy", out])
    return out


async def subtitle_extract(input_path: str) -> str:
    out = _out_path(input_path, "subs", ext="srt")
    await _run(["ffmpeg", "-y", "-i", input_path, "-map", "0:s", out])
    return out


async def subtitle_remove(input_path: str) -> str:
    out = _out_path(input_path, "nosubs")
    await _run(["ffmpeg", "-y", "-i", input_path, "-sn", "-c", "copy", out])
    return out


async def convert_video(input_path: str, target_format: str) -> str:
    out = _out_path(input_path, "converted", ext=target_format)
    await _run([
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac",
        out,
    ])
    return out


async def convert_audio(input_path: str, target_format: str) -> str:
    return await audio_extract(input_path, target_format)


async def generate_screenshot(input_path: str, timestamp: str = "00:00:01") -> str:
    out = _out_path(input_path, f"ss_{timestamp.replace(':', '-')}", ext="jpg")
    await _run(["ffmpeg", "-y", "-ss", timestamp, "-i", input_path, "-vframes", "1", out])
    return out


async def generate_screenshots(input_path: str, count: int = 4, duration: float = None) -> list:
    """Generate `count` evenly spaced screenshots across the video."""
    if duration is None:
        duration = await get_duration(input_path)
    paths = []
    step = duration / (count + 1)
    for i in range(1, count + 1):
        ts = step * i
        h, rem = divmod(int(ts), 3600)
        m, s = divmod(rem, 60)
        timestamp = f"{h:02}:{m:02}:{s:02}"
        path = await generate_screenshot(input_path, timestamp)
        paths.append(path)
    return paths


async def sample_video(input_path: str, duration: int = 60) -> str:
    out = _out_path(input_path, "sample")
    await _run(["ffmpeg", "-y", "-i", input_path, "-ss", "0", "-t", str(duration), "-c", "copy", out])
    return out


async def compress_video(input_path: str, crf: int = 28) -> str:
    out = _out_path(input_path, "compressed")
    await _run([
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k",
        out,
    ])
    return out


async def embed_metadata(input_path: str, title=None, artist=None, album=None, year=None) -> str:
    out = _out_path(input_path, "meta")
    cmd = ["ffmpeg", "-y", "-i", input_path]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if artist:
        cmd += ["-metadata", f"artist={artist}"]
    if album:
        cmd += ["-metadata", f"album={album}"]
    if year:
        cmd += ["-metadata", f"date={year}"]
    cmd += ["-c", "copy", out]
    await _run(cmd)
    return out


async def get_duration(input_path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0
