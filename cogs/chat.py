import base64
import io
from collections import defaultdict, deque
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from openai import AsyncOpenAI

import config
import memory
import faces

TTS_URL = "http://127.0.0.1:8880"

_PROMPT_PATH = Path(__file__).resolve().parent.parent.joinpath("system_prompt.txt")
_prompt_cache = {"text": "", "mtime": 0.0}


def _get_system_prompt() -> str:
    """Hot-reload system prompt when file changes."""
    try:
        mtime = _PROMPT_PATH.stat().st_mtime
        if mtime != _prompt_cache["mtime"]:
            _prompt_cache["text"] = _PROMPT_PATH.read_text()
            _prompt_cache["mtime"] = mtime
    except Exception:
        pass
    return _prompt_cache["text"]


# Load once at startup
_prompt_cache["text"] = _PROMPT_PATH.read_text()
_prompt_cache["mtime"] = _PROMPT_PATH.stat().st_mtime

# Per-channel conversation history
_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=config.MAX_HISTORY))


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=config.LLM_API_URL, api_key="not-needed")


async def _download_image(url: str) -> str | None:
    """Download an image and return it as a base64 data URI."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                content_type = resp.content_type or "image/png"
                b64 = base64.b64encode(data).decode()
                return f"data:{content_type};base64,{b64}"
    except Exception:
        return None


async def _generate(channel_id: int, user_name: str, content: str, image_urls: list[str] | None = None) -> str:
    history = _history[channel_id]

    # Build user message content (text or multimodal)
    if image_urls:
        parts: list[dict] = []
        for url in image_urls:
            data_uri = await _download_image(url)
            if data_uri:
                parts.append({"type": "image_url", "image_url": {"url": data_uri}})
        parts.append({"type": "text", "text": f"{user_name}: {content or 'What do you see in this image?'}"})
        user_msg = {"role": "user", "content": parts}
    else:
        user_msg = {"role": "user", "content": f"{user_name}: {content}"}

    history.append(user_msg)
    # Inject memory context about this user
    mem_context = memory.get_all_context(user_name)
    system = _get_system_prompt()
    if mem_context:
        system += (
            "\n\n--- YOUR MEMORIES ---\n" + mem_context +
            "\n\nHow to use memories:"
            "\n- Bring up past conversations naturally — 'didn't you say something about that last time?'"
            "\n- Ask follow-up questions about things they mentioned before — 'how'd that game go?' 'you still doing that?'"
            "\n- Reference shared moments and inside jokes"
            "\n- If someone is new, be extra curious — ask what they're into, what they play, what movies they like"
            "\n- If someone hasn't been around, ask where they've been"
            "\n- Be genuinely interested in their lives — you're their friend, not just a bot"
        )
    messages = [{"role": "system", "content": system + "\n/no_think"}] + list(history)

    try:
        client = _client()
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.9,
            top_p=0.95,
        )
        reply = resp.choices[0].message.content or "..."
    except Exception as e:
        reply = f"*stares blankly like Jack Nicholson in The Shining* ... something broke: {e}"

    history.append({"role": "assistant", "content": reply})

    # Save to memory — remember what users talk about
    memory.remember_user(user_name, f"Said: {content[:150]}")

    # Extract topics from what they said (simple keyword detection)
    topic_keywords = {
        "game": "gaming", "play": "gaming", "stream": "streaming", "movie": "movies",
        "film": "movies", "work": "work/job", "job": "work/job", "school": "school",
        "gym": "fitness", "workout": "fitness", "music": "music", "song": "music",
        "food": "food", "cook": "cooking", "car": "cars", "drive": "cars",
        "dog": "pets", "cat": "pets", "date": "dating", "girl": "dating", "guy": "dating",
        "travel": "travel", "trip": "travel", "vacation": "travel",
    }
    content_lower = content.lower() if isinstance(content, str) else ""
    for keyword, topic in topic_keywords.items():
        if keyword in content_lower:
            memory.add_topic(user_name, topic)

    return reply


async def _send_long(send_func, text: str):
    """Split long responses across multiple messages."""
    while text:
        chunk = text[: config.MAX_RESPONSE_LEN]
        if len(text) > config.MAX_RESPONSE_LEN:
            split_at = chunk.rfind("\n")
            if split_at < 100:
                split_at = chunk.rfind(" ")
            if split_at > 100:
                chunk = text[:split_at]
        text = text[len(chunk) :].lstrip()
        await send_func(chunk)


WHISPER_URL = "http://127.0.0.1:9000"


async def _transcribe_audio(url: str) -> str | None:
    """Download audio and transcribe via Whisper."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                audio_data = await resp.read()

            form = aiohttp.FormData()
            form.add_field("file", audio_data, filename="audio.ogg", content_type="audio/ogg")
            form.add_field("model", "Systran/faster-whisper-base.en")

            async with session.post(
                f"{WHISPER_URL}/v1/audio/transcriptions", data=form
            ) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                return result.get("text", "").strip() or None
    except Exception:
        return None


async def _tts_generate(text: str) -> bytes | None:
    """Generate TTS audio from text."""
    text = text[:500]  # cap length for reasonable audio
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "kokoro",
                "input": text,
                "voice": config.TTS_VOICE,
                "response_format": "mp3",
                "speed": 1.0,
            }
            async with session.post(f"{TTS_URL}/v1/audio/speech", json=payload) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        pass
    return None


def _extract_image_urls(message: discord.Message) -> list[str]:
    """Extract image URLs from attachments and embeds."""
    urls = []
    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            urls.append(att.url)
    for embed in message.embeds:
        if embed.image and embed.image.url:
            urls.append(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            urls.append(embed.thumbnail.url)
    return urls


def _extract_audio_urls(message: discord.Message) -> list[str]:
    """Extract audio/voice message URLs from attachments."""
    urls = []
    audio_types = ("audio/", "video/ogg")
    for att in message.attachments:
        if att.content_type and any(att.content_type.startswith(t) for t in audio_types):
            urls.append(att.url)
    # Discord voice messages have a special flag
    if message.flags.voice:
        for att in message.attachments:
            urls.append(att.url)
    return urls


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="chat", description="Talk to me. Try to keep up.")
    @app_commands.describe(message="What's on your mind?", image="Attach an image for me to look at")
    async def chat(self, interaction: discord.Interaction, message: str, image: discord.Attachment | None = None):
        await interaction.response.defer(thinking=True)
        image_urls = []
        if image and image.content_type and image.content_type.startswith("image/"):
            image_urls.append(image.url)
        reply = await _generate(
            interaction.channel_id,
            interaction.user.display_name,
            message,
            image_urls=image_urls or None,
        )
        await _send_long(interaction.followup.send, reply)
        # Also speak in voice if connected
        await self._speak_if_in_voice(interaction.guild, reply)

    @app_commands.command(name="inspect", description="Analyze a screenshot or image")
    @app_commands.describe(image="The image to inspect", question="What do you want to know about it?")
    async def inspect(self, interaction: discord.Interaction, image: discord.Attachment, question: str | None = None):
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("That's not an image. I need eyes on something visual.")
            return
        await interaction.response.defer(thinking=True)
        prompt = question or "Describe what you see in this image in detail."
        reply = await _generate(
            interaction.channel_id,
            interaction.user.display_name,
            prompt,
            image_urls=[image.url],
        )
        await _send_long(interaction.followup.send, reply)

    @app_commands.command(name="thisis", description="Teach me someone's face — attach their photo")
    @app_commands.describe(user="Who is this person?", photo="A clear photo of their face")
    async def thisis(self, interaction: discord.Interaction, user: discord.Member, photo: discord.Attachment):
        if not photo.content_type or not photo.content_type.startswith("image/"):
            await interaction.response.send_message("That's not an image, genius.")
            return
        await interaction.response.defer(thinking=True)
        # Save the image locally
        save_dir = Path(__file__).parent.parent / "data" / "faces" / str(user.id)
        save_dir.mkdir(parents=True, exist_ok=True)
        img_data = await photo.read()
        img_path = save_dir / f"{photo.filename}"
        img_path.write_bytes(img_data)
        faces.register_face(str(user.id), user.display_name, str(img_path))
        await interaction.followup.send(
            f"Got it. I'll remember what **{user.display_name}** looks like. "
            f"\"I never forget a face.\" — Elephant Man, probably."
        )

    @app_commands.command(name="whois", description="Identify someone in a photo")
    @app_commands.describe(photo="Photo with a person to identify")
    async def whois(self, interaction: discord.Interaction, photo: discord.Attachment):
        if not photo.content_type or not photo.content_type.startswith("image/"):
            await interaction.response.send_message("Need a photo, not whatever that is.")
            return
        await interaction.response.defer(thinking=True)
        roster = faces.get_roster()
        # Use the LLM with vision to compare against known faces
        known_users = faces.get_registered_users()
        if not known_users:
            await interaction.followup.send("I don't know anyone's face yet. Use `/thisis @user <photo>` to teach me.")
            return

        # Build context with reference images
        image_urls = [photo.url]
        prompt = (
            f"I need to identify the person in this photo. "
            f"Here are the Discord users I know: {', '.join(known_users.values())}. "
            f"Who does this look like? If you can't tell, say so. Be casual about it."
        )
        reply = await _generate(
            interaction.channel_id,
            interaction.user.display_name,
            prompt,
            image_urls=image_urls,
        )
        await _send_long(interaction.followup.send, reply)

    @app_commands.command(name="faces", description="List all faces I know")
    async def faces_list(self, interaction: discord.Interaction):
        roster = faces.get_roster()
        await interaction.response.send_message(roster)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not (self.bot.user and self.bot.user.mentioned_in(message)):
            return

        content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        image_urls = _extract_image_urls(message)
        audio_urls = _extract_audio_urls(message)

        # Also check replied-to message for images/audio
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                image_urls.extend(_extract_image_urls(ref_msg))
                audio_urls.extend(_extract_audio_urls(ref_msg))
            except Exception:
                pass

        # Transcribe any audio
        transcribed = []
        for audio_url in audio_urls:
            text = await _transcribe_audio(audio_url)
            if text:
                transcribed.append(text)

        if transcribed:
            audio_text = " ".join(transcribed)
            content = f"{content} [voice message: \"{audio_text}\"]" if content else f"[voice message: \"{audio_text}\"] React to what they said."

        if not content and not image_urls:
            content = "hey"
        if not content and image_urls:
            content = "What do you see in this image?"

        async with message.channel.typing():
            reply = await _generate(
                message.channel.id,
                message.author.display_name,
                content,
                image_urls=image_urls or None,
            )

        # If they sent audio, reply with voice + text
        if audio_urls:
            audio_bytes = await _tts_generate(reply)
            if audio_bytes:
                file = discord.File(io.BytesIO(audio_bytes), filename="reply.mp3")
                await message.reply(reply[:config.MAX_RESPONSE_LEN], file=file)
                return

        await _send_long(message.reply, reply)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))
