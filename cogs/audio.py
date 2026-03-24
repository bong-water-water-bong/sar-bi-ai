import io
import os
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips"
CLIPS_DIR.mkdir(exist_ok=True)

TTS_URL = "http://127.0.0.1:8880"
TTS_VOICE = config.TTS_VOICE


def _list_clips() -> list[str]:
    """List available meme clips by name (without extension)."""
    exts = {".mp3", ".wav", ".ogg", ".m4a"}
    return sorted(
        f.stem for f in CLIPS_DIR.iterdir() if f.suffix.lower() in exts
    )


def _get_clip_path(name: str) -> Path | None:
    """Find a clip file by stem name."""
    for f in CLIPS_DIR.iterdir():
        if f.stem.lower() == name.lower():
            return f
    return None


async def _tts_generate(text: str) -> bytes | None:
    """Generate a short TTS clip via Kokoro. Max ~10s of speech."""
    # Limit text to keep it under 10s
    text = text[:200]
    async with aiohttp.ClientSession() as session:
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": TTS_VOICE,
            "response_format": "mp3",
            "speed": 1.0,
        }
        async with session.post(
            f"{TTS_URL}/v1/audio/speech", json=payload
        ) as resp:
            if resp.status == 200:
                return await resp.read()
    return None


class AudioCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="clips", description="List available meme audio clips"
    )
    async def clips(self, interaction: discord.Interaction):
        clip_list = _list_clips()
        if not clip_list:
            await interaction.response.send_message(
                "No clips yet. Drop some .mp3/.wav files into the `clips/` folder, "
                "or use `/tts` to generate speech on the fly."
            )
            return
        formatted = "\n".join(f"- `{c}`" for c in clip_list)
        await interaction.response.send_message(
            f"**Available meme clips:**\n{formatted}\n\nUse `/play <name>` to play one."
        )

    @app_commands.command(
        name="clip", description="Play a meme audio clip in voice chat"
    )
    @app_commands.describe(name="Name of the clip to play")
    async def clip(self, interaction: discord.Interaction, name: str):
        clip_path = _get_clip_path(name)
        if not clip_path:
            available = ", ".join(_list_clips()[:10]) or "none yet"
            await interaction.response.send_message(
                f"Clip `{name}` not found. Available: {available}"
            )
            return

        # Check if user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "\"You talkin' to me?\" Get in a voice channel first."
            )
            return

        await interaction.response.defer(thinking=True)
        vc = None
        try:
            channel = interaction.user.voice.channel
            vc = await channel.connect()
            source = discord.FFmpegPCMAudio(str(clip_path))
            vc.play(source)
            # Wait for playback to finish (max 10s)
            waited = 0
            while vc.is_playing() and waited < 10:
                await __import__("asyncio").sleep(0.5)
                waited += 0.5
            await interaction.followup.send(f"Played `{name}`")
        except Exception as e:
            await interaction.followup.send(f"Audio failed: `{e}`")
        finally:
            if vc and vc.is_connected():
                await vc.disconnect()

    @app_commands.command(
        name="tts", description="Generate and play a short text-to-speech clip"
    )
    @app_commands.describe(text="What should I say? (max 10s)")
    async def tts(self, interaction: discord.Interaction, text: str):
        # If user is in voice, play it. Otherwise, send as file.
        await interaction.response.defer(thinking=True)

        audio_bytes = await _tts_generate(text)
        if not audio_bytes:
            await interaction.followup.send(
                "TTS engine choked. Even HAL 9000 had better days."
            )
            return

        if interaction.user.voice and interaction.user.voice.channel:
            # Save temp file and play in VC
            tmp = Path("/tmp/tts_output.mp3")
            tmp.write_bytes(audio_bytes)
            vc = None
            try:
                vc = await interaction.user.voice.channel.connect()
                source = discord.FFmpegPCMAudio(str(tmp))
                vc.play(source)
                waited = 0
                while vc.is_playing() and waited < 10:
                    await __import__("asyncio").sleep(0.5)
                    waited += 0.5
                await interaction.followup.send(f"*\"{text[:80]}\"*")
            except Exception as e:
                await interaction.followup.send(f"Voice playback failed: `{e}`")
            finally:
                if vc and vc.is_connected():
                    await vc.disconnect()
                tmp.unlink(missing_ok=True)
        else:
            file = discord.File(io.BytesIO(audio_bytes), filename="tts.mp3")
            await interaction.followup.send(
                f"*\"{text[:80]}\"*\n(Join a voice channel and I'll play it live next time)",
                file=file,
            )

    @app_commands.command(
        name="upload_clip", description="Upload an audio clip to the meme library"
    )
    @app_commands.describe(
        name="Name for this clip",
        file="Audio file (mp3/wav/ogg, max 10s)",
    )
    async def upload_clip(
        self, interaction: discord.Interaction, name: str, file: discord.Attachment
    ):
        if not file.content_type or not file.content_type.startswith("audio/"):
            await interaction.response.send_message("That's not an audio file, chief.")
            return
        if file.size > 5_000_000:  # 5MB max
            await interaction.response.send_message("Too big. Keep it under 5MB / 10s.")
            return

        await interaction.response.defer(thinking=True)
        ext = Path(file.filename).suffix or ".mp3"
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_").lower()
        dest = CLIPS_DIR / f"{safe_name}{ext}"
        data = await file.read()
        dest.write_bytes(data)

        # Also add to Discord soundboard
        soundboard_msg = ""
        if interaction.guild:
            try:
                sound = await interaction.guild.create_soundboard_sound(
                    name=safe_name,
                    sound=data,
                    reason=f"Meme clip uploaded by {interaction.user.display_name}",
                )
                soundboard_msg = f" + added to soundboard"
            except discord.Forbidden:
                soundboard_msg = " (no permission to add to soundboard — give me Manage Guild)"
            except Exception as e:
                soundboard_msg = f" (soundboard failed: {e})"

        await interaction.followup.send(
            f"Clip `{safe_name}` saved{soundboard_msg}. Use `/clip {safe_name}` to unleash it."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AudioCog(bot))
