"""Earworm Warfare — plays catchy song snippets during silence in voice chat."""

import asyncio
import logging
import os
import random
import subprocess
import time
from pathlib import Path

import discord
from discord.ext import commands

log = logging.getLogger("earworm")

EARWORM_DIR = Path("/home/bcloud/discord-bot/clips/earworms")
EARWORM_DIR.mkdir(parents=True, exist_ok=True)

# Earworm songs — short catchy hooks that get stuck in your head
# These will be downloaded via yt-dlp and trimmed to the catchiest 10-15 seconds
EARWORMS = [
    {"name": "never_gonna", "query": "Rick Astley Never Gonna Give You Up", "start": "43", "duration": "12"},
    {"name": "baby_shark", "query": "Baby Shark doo doo", "start": "5", "duration": "10"},
    {"name": "its_a_small_world", "query": "Its A Small World After All Disney", "start": "5", "duration": "12"},
    {"name": "macarena", "query": "Macarena Los Del Rio", "start": "3", "duration": "10"},
    {"name": "barbie_girl", "query": "Barbie Girl Aqua", "start": "25", "duration": "10"},
    {"name": "hamster_dance", "query": "Hampster Dance Song", "start": "10", "duration": "12"},
    {"name": "crazy_frog", "query": "Crazy Frog Axel F", "start": "14", "duration": "10"},
    {"name": "nyan_cat", "query": "Nyan Cat original", "start": "3", "duration": "10"},
    {"name": "what_is_love", "query": "Haddaway What Is Love", "start": "25", "duration": "10"},
    {"name": "tequila", "query": "The Champs Tequila", "start": "5", "duration": "8"},
    {"name": "sandstorm", "query": "Darude Sandstorm", "start": "27", "duration": "10"},
    {"name": "oh_no", "query": "Oh No Oh No Oh No No No tiktok", "start": "0", "duration": "8"},
    {"name": "careless_whisper", "query": "George Michael Careless Whisper sax", "start": "0", "duration": "12"},
]


def download_earworms():
    """Download and trim earworm clips."""
    for ew in EARWORMS:
        out_path = EARWORM_DIR / f"{ew['name']}.mp3"
        if out_path.exists():
            continue
        log.info(f"Downloading earworm: {ew['name']}")
        try:
            # Download with yt-dlp
            tmp_path = f"/tmp/earworm_{ew['name']}.webm"
            subprocess.run([
                "yt-dlp", "-x", "--audio-format", "mp3",
                "-o", f"/tmp/earworm_{ew['name']}.%(ext)s",
                f"ytsearch1:{ew['query']}",
            ], capture_output=True, timeout=60)

            # Find the downloaded file
            for ext in [".mp3", ".webm", ".m4a", ".opus"]:
                src = f"/tmp/earworm_{ew['name']}{ext}"
                if os.path.exists(src):
                    break
            else:
                log.warning(f"Could not find downloaded file for {ew['name']}")
                continue

            # Trim to the catchy part
            subprocess.run([
                "ffmpeg", "-y", "-i", src,
                "-ss", ew["start"], "-t", ew["duration"],
                "-af", "afade=t=in:st=0:d=0.5,afade=t=out:st=" + str(int(ew["duration"]) - 1) + ":d=1,volume=0.4",
                str(out_path),
            ], capture_output=True, timeout=30)

            # Cleanup
            os.unlink(src) if os.path.exists(src) else None
            log.info(f"Earworm ready: {ew['name']}")
        except Exception as e:
            log.error(f"Failed to download {ew['name']}: {e}")


def get_random_earworm() -> Path | None:
    """Get a random earworm clip."""
    clips = list(EARWORM_DIR.glob("*.mp3"))
    return random.choice(clips) if clips else None


import aiohttp

TTS_URL = "http://127.0.0.1:8880"
TTS_VOICE = "af_bella"

# Humming patterns that match song melodies using "mmm" sounds
HUMMING_PATTERNS = {
    "never_gonna": "Mmm mmm mmm mm mm mmm, mmm mmm mmm mm mm mmm, mmm mmm mmm mm mm mmm mmm, mmm mm mm mmm mmm mmm",
    "baby_shark": "Mm mm mm mm mm mm, mm mm mm mm mm mm, mm mm mm mm mm mm, mm mm mm mm mm",
    "its_a_small_world": "Mmm mm mm mmm mmm mmm, mmm mm mm mmm mmm mmm, mmm mm mm mmm mmm mmm, mm mm mmm mmm mmm",
    "macarena": "Mmm mm mm mm mm mm mm mmm mmm, mm mm mm mm mm mm mm mmm mmm, mm mm mm mm mm mm mmm mmm",
    "barbie_girl": "Mm mm mm mm mmm mmm mmm, mm mm mm mm mmm mmm, mm mm mmm mm mmm mmm, mm mmm mmm",
    "hamster_dance": "Mm mm mmmm mm, mm mm mmmm mm, mm mm mmmm mm mm mm mm mm mm",
    "crazy_frog": "Mm mm mm mmm mm mm, mm mm mm mmm mm mm, mm mm mm mmm mm mm mm mm",
    "what_is_love": "Mmm mm mmm mmm, mmm mm mmm mmm, mmm mm mmm mmm, mmm mm mmm",
    "careless_whisper": "Mm mmm mmm mmm, mm mm mmm mmm mmm, mm mm mmm mmm mmm",
    "sandstorm": "Mm mm mm mm, mmm mmm mmm mmm, mm mm mm mm, mmm mmm mmm mmm",
    "oh_no": "Mm mm, mm mm, mm mm mm mm",
    "tequila": "Mm mm mm mm mmm, mm mm mm mm mmm, mm mm mm mmm mm mmm",
    "nyan_cat": "Mm mm mm mm mm mmm mm, mm mm mm mm mm mmm mm, mm mm mm mm mm mmm mm mm",
}

HUMMING_DIR = Path("/home/bcloud/discord-bot/clips/earworms/hummed")
HUMMING_DIR.mkdir(parents=True, exist_ok=True)


async def generate_hummed_versions():
    """Generate TTS humming versions of earworm songs."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, pattern in HUMMING_PATTERNS.items():
            out_path = HUMMING_DIR / f"{name}_hummed.mp3"
            if out_path.exists():
                continue
            try:
                payload = {
                    "model": "kokoro",
                    "input": pattern,
                    "voice": TTS_VOICE,
                    "response_format": "mp3",
                    "speed": 1.0,
                }
                async with session.post(f"{TTS_URL}/v1/audio/speech", json=payload) as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        out_path.write_bytes(audio)
                        log.info(f"Generated hummed earworm: {name}")
            except Exception as e:
                log.error(f"Failed to generate hummed {name}: {e}")


def get_random_hummed_earworm() -> Path | None:
    """Get a random hummed earworm clip."""
    clips = list(HUMMING_DIR.glob("*_hummed.mp3"))
    return random.choice(clips) if clips else None
