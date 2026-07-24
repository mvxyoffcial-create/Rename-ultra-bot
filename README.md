# Telegram File Rename Bot

Pyrogram v2 + MongoDB (motor) + ffmpeg. Rename files, manage permanent
thumbnail/metadata/caption/prefix/suffix, convert formats, extract/remove
audio/subtitle/video streams, generate screenshots, sample clips, and
compress — all with a boxed progress bar and cancel button.

## What's included

```
rename_bot/
├── bot.py              # Main bot: all commands, handlers, rename flow
├── config.py            # Configuration (reads from .env)
├── database.py           # MongoDB (motor) async data layer
├── ffmpeg_utils.py       # All ffmpeg-backed media operations
├── progress.py           # Progress bar rendering + throttled updates
├── requirements.txt
├── .env.example           # Copy to .env and fill in your values
└── downloads/            # Temp folder for in-flight files (auto-created)
```

## Setup

1. **Install ffmpeg** on your system (`apt install ffmpeg` / `brew install ffmpeg`).
2. **Install Python deps:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure credentials** — copy `.env.example` to `.env` and fill in:
   - `API_ID` / `API_HASH` from https://my.telegram.org
   - `BOT_TOKEN` from [@BotFather](https://t.me/BotFather)
   - `MONGO_URL` — a MongoDB Atlas connection string (or self-hosted)
   - `ADMIN_IDS` — your Telegram user ID(s), comma-separated
4. **Run it:**
   ```bash
   python bot.py
   ```

## Notes on the build

- **No force-subscribe / mandatory channel join** is implemented — every
  command works immediately, with no requirement to join any channel.
- **"500 workers"**: Pyrogram's internal update-handling thread pool is set
  to 500 as requested (`PYROGRAM_WORKERS` in `.env`), but actual ffmpeg
  transcoding concurrency is capped separately via
  `MAX_CONCURRENT_FFMPEG` (default 3) — running hundreds of simultaneous
  video transcodes will crash almost any real server, so this is kept
  configurable and sane by default. Raise it if your hardware can handle it.
- **File size limit**: 4GB, matching Telegram Bot API limits for bot uploads
  when using a local Bot API server; standard cloud Bot API caps uploads at
  50MB, downloads at 20MB — if you need true 2–4GB support you'll need to
  run your own [local Bot API server](https://github.com/tdlib/telegram-bot-api)
  and point Pyrogram at it.

## Commands

`/start` `/help` `/about` `/info` `/settings` `/ping`
`/thumbnail` `/delthumbnail` `/metadata` `/delmetadata`
`/caption` `/delcaption` `/prefix` `/suffix`
`/batch` `/donebatch`
`/stats` `/broadcast` `/ban <id>` `/unban <id>` (admin only)

## How renaming works

1. Send any file (document/video/audio).
2. Reply to the bot's confirmation message with the new filename
   (including extension), e.g. `My Show S01E01.mkv`.
3. The bot downloads with a live progress bar, applies your saved
   prefix/suffix/thumbnail/metadata/caption, then uploads with another
   progress bar. Tap ❌ Cancel any time to stop.

Batch mode: `/batch` → send files one by one → `/donebatch` → reply with a
name pattern (`Episode {n}` for numbered files, or a plain name for
sequential naming).

Media tools (stream/audio/subtitle extract & remove, screenshots, sample
clips, compression) and format conversion are available via inline buttons
under any file you send.
