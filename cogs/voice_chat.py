import asyncio
import io
import logging
import random
import struct
import time
import wave
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient, BasicSink, AudioSink, VoiceData

import config
import memory
import earworm

log = logging.getLogger("voice_chat")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

TTS_URL = "http://127.0.0.1:8880"
WHISPER_URL = "http://127.0.0.1:9000"
LLM_URL = config.LLM_API_URL

# Silence threshold — seconds of no audio before processing
SILENCE_THRESHOLD = 0.6
# Min audio length to bother transcribing (seconds)
MIN_AUDIO_LENGTH = 0.3
# Discord voice: 48kHz, stereo, 16-bit signed PCM
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 192000


class UserAudioBuffer:
    """Buffers PCM audio per user and detects silence."""

    def __init__(self):
        self.chunks: list[bytes] = []
        self.last_packet_time: float = 0
        self.started: float = 0

    def add(self, pcm_data: bytes):
        now = time.monotonic()
        if not self.chunks:
            self.started = now
        self.chunks.append(pcm_data)
        self.last_packet_time = now

    def silence_duration(self) -> float:
        if not self.last_packet_time:
            return 0
        return time.monotonic() - self.last_packet_time

    def duration(self) -> float:
        total_bytes = sum(len(c) for c in self.chunks)
        return total_bytes / BYTES_PER_SECOND if BYTES_PER_SECOND else 0

    def to_wav(self) -> bytes:
        import subprocess
        raw = b"".join(self.chunks)
        raw_path = "/tmp/_voice_raw.pcm"
        wav_path = "/tmp/_voice_out.wav"
        with open(raw_path, "wb") as f:
            f.write(raw)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", raw_path,
                    "-ar", "16000", "-ac", "1",
                    "-af", "highpass=f=80,lowpass=f=8000,loudnorm",
                    wav_path,
                ],
                check=True, timeout=10,
            )
            return Path(wav_path).read_bytes()
        except Exception:
            # Fallback: simple manual conversion
            import struct as _struct
            samples = _struct.unpack(f"<{len(raw)//2}h", raw)
            mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)]
            downsampled = mono[::3]
            mono_raw = _struct.pack(f"<{len(downsampled)}h", *downsampled)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(16000)
                wf.writeframes(mono_raw)
            return buf.getvalue()

    def clear(self):
        self.chunks.clear()
        self.last_packet_time = 0
        self.started = 0


# Per-guild state
class GuildVoiceState:
    def __init__(self):
        self.vc: VoiceRecvClient | None = None
        self.buffers: dict[int, UserAudioBuffer] = defaultdict(UserAudioBuffer)
        self.is_speaking: bool = False  # True when bot is talking
        self.active: bool = False
        self.history: list[dict] = []
        self.text_channel: discord.abc.Messageable | None = None


_states: dict[int, GuildVoiceState] = defaultdict(GuildVoiceState)

from pathlib import Path as _Path

Path = _Path
_PROMPT_PATH = Path("/home/<YOUR_USER>/discord-bot/system_prompt.txt")
_prompt_cache = {"text": _PROMPT_PATH.read_text(), "mtime": _PROMPT_PATH.stat().st_mtime}


def _get_system_prompt() -> str:
    try:
        mtime = _PROMPT_PATH.stat().st_mtime
        if mtime != _prompt_cache["mtime"]:
            _prompt_cache["text"] = _PROMPT_PATH.read_text()
            _prompt_cache["mtime"] = mtime
    except Exception:
        pass
    return _prompt_cache["text"]


VOICE_ADDENDUM = (
    "\nYou are in a LIVE voice chat right now. Rules for voice:"
    "\n- ONE sentence max. Like texting but out loud."
    "\n- Sound human. Use filler words naturally: 'oh', 'yeah', 'nah', 'dude', 'honestly', 'like'"
    "\n- React emotionally — laugh (haha), groan, sigh, gasp"
    "\n- Match their energy. If they're hyped, be hyped. If chill, be chill."
    "\n- Use contractions always (don't, won't, can't, that's, it's)"
    "\n- Never sound like you're reading. Sound like you're hanging out."
    "\n- Interrupt style is fine — short reactions like 'no way', 'bruh', 'that's wild'"
)


# Persistent HTTP sessions to avoid reconnection overhead
_http_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=3)
        _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session

async def _transcribe(wav_data: bytes) -> str | None:
    """Send WAV to Whisper and get transcription."""
    try:
        session = await _get_session()
        form = aiohttp.FormData()
        form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", "Systran/faster-whisper-tiny.en")
        async with session.post(f"{WHISPER_URL}/v1/audio/transcriptions", data=form) as resp:
            if resp.status == 200:
                result = await resp.json()
                text = result.get("text", "").strip()
                if text and len(text) > 2 and text.lower() not in (
                    "you", "thank you.", "thanks for watching!", "bye.",
                    "...", "thank you for watching.", "thanks for watching.",
                ):
                    return text
    except Exception:
        pass
    return None


async def _llm_reply(history: list[dict], user_name: str, text: str) -> str:
    """Get LLM response for voice conversation."""
    history.append({"role": "user", "content": f"{user_name}: {text}"})
    # Keep history manageable
    if len(history) > 20:
        history[:] = history[-14:]

    mem_context = memory.get_all_context(user_name)
    system = _get_system_prompt() + VOICE_ADDENDUM
    if mem_context:
        system += "\n\n--- YOUR MEMORIES ---\n" + mem_context + "\nBring up past moments naturally."
    # Add /no_think tag to disable Qwen3 thinking mode
    messages = [{"role": "system", "content": system + "\n/no_think"}] + history

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=LLM_URL, api_key="not-needed")
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            max_tokens=100,
            temperature=0.8,
        )
        msg = resp.choices[0].message
        reply = msg.content or ""
        # Qwen3 thinking model: if content is empty, check reasoning_content
        if not reply.strip():
            reasoning = getattr(msg, "reasoning_content", "") or ""
            if reasoning:
                # Extract any actual response from end of reasoning
                reply = reasoning.strip().split("\n")[-1]
        if not reply.strip():
            reply = "hmm"
        # Trim to one sentence for voice
        for sep in [". ", "! ", "? "]:
            if sep in reply:
                reply = reply[:reply.index(sep) + 1]
                break
    except Exception as e:
        log.error(f"LLM error: {type(e).__name__}: {e}")
        reply = "sorry, brain fart. what were you saying?"

    history.append({"role": "assistant", "content": reply})
    memory.remember_user(user_name, f"Said in voice: {text[:100]}")
    return reply


async def _tts(text: str) -> bytes | None:
    """Generate TTS audio."""
    try:
        session = await _get_session()
        payload = {
                "model": "kokoro",
                "input": text[:300],
                "voice": config.TTS_VOICE,
                "response_format": "mp3",
                "speed": 1.15,
            }
        async with session.post(f"{TTS_URL}/v1/audio/speech", json=payload) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


class VoiceChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._poll_tasks: dict[int, asyncio.Task] = {}

    @app_commands.command(name="join", description="Join your voice channel for a conversation")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "\"You talkin' to me?\" Get in a voice channel first, De Niro."
            )
            return

        state = _states[interaction.guild_id]
        channel = interaction.user.voice.channel

        if state.vc and state.vc.is_connected():
            await state.vc.move_to(channel)
            await interaction.response.send_message(f"Moved to **{channel.name}**. I'm listening.")
            return

        await interaction.response.defer(thinking=True)

        try:
            state.vc = await channel.connect(cls=VoiceRecvClient)
            state.active = True
            state.text_channel = interaction.channel
            state.history.clear()

            # Set up audio callback
            _pkt_count = [0]

            def on_audio(user, data):
                if not state.active or state.is_speaking:
                    return
                _pkt_count[0] += 1
                if _pkt_count[0] % 500 == 0:
                    log.info(f"Audio packets received: {_pkt_count[0]}")
                if user and not user.bot and data.pcm:
                    state.buffers[user.id].add(data.pcm)

            state.vc.listen(BasicSink(on_audio))
            log.info(f"Listening in {channel.name} ({channel.guild.name})")

            # Start polling for silence
            task = asyncio.create_task(self._poll_silence(interaction.guild_id))
            self._poll_tasks[interaction.guild_id] = task

            await interaction.followup.send(
                f"Joined **{channel.name}**. Talk to me — I'm all ears. "
                "\"We're gonna need a bigger boat\" — or at least a decent mic."
            )
        except Exception as e:
            await interaction.followup.send(f"Couldn't join: `{e}`")

    @app_commands.command(name="say", description="Make me say something out loud in voice chat")
    @app_commands.describe(text="What should I say?")
    async def say(self, interaction: discord.Interaction, text: str):
        state = _states[interaction.guild_id]
        if not state.vc or not state.vc.is_connected():
            await interaction.response.send_message("I'm not in a voice channel. Use `/join` first.")
            return
        await interaction.response.defer(thinking=True)
        audio = await _tts(text)
        if audio:
            tmp = f"/tmp/voice_say_{interaction.guild_id}.mp3"
            with open(tmp, "wb") as f:
                f.write(audio)
            done = asyncio.Event()
            state.vc.play(
                discord.FFmpegPCMAudio(tmp, before_options="-nostdin", options="-vn"),
                after=lambda e: self.bot.loop.call_soon_threadsafe(done.set),
            )
            await asyncio.wait_for(done.wait(), timeout=30)
            await interaction.followup.send(f"*\"{text[:100]}\"*")
        else:
            await interaction.followup.send("TTS broke. Even I'm speechless.")

    @app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        state = _states[interaction.guild_id]
        state.active = False

        if interaction.guild_id in self._poll_tasks:
            self._poll_tasks[interaction.guild_id].cancel()
            del self._poll_tasks[interaction.guild_id]

        if state.vc and state.vc.is_connected():
            state.vc.stop_listening()
            await state.vc.disconnect()
            state.vc = None
            state.buffers.clear()
            await interaction.response.send_message(
                "\"I'll be back.\" — but for now, I'm out."
            )
        else:
            await interaction.response.send_message("I'm not in a voice channel.")

    async def _poll_silence(self, guild_id: int):
        """Poll audio buffers and process when silence is detected."""
        state = _states[guild_id]
        last_activity = time.monotonic()
        hum_cooldown = 0  # Don't hum again too soon

        # Earworm warfare — generate hummed versions on first run
        earworm.download_earworms()
        asyncio.create_task(earworm.generate_hummed_versions())
        HUM_DIR = Path(__file__).parent.parent / "clips" / "hums"
        HUM_FILES = sorted(HUM_DIR.glob("*_trimmed.mp3")) if HUM_DIR.exists() else []

        while state.active:
            await asyncio.sleep(0.05)

            if not state.vc or not state.vc.is_connected():
                break
            if state.is_speaking:
                last_activity = time.monotonic()
                continue

            # Check if anyone is talking
            anyone_talking = any(buf.chunks and buf.silence_duration() < 1.0 for buf in state.buffers.values())
            if anyone_talking:
                last_activity = time.monotonic()
                # Stop humming if someone speaks
                if state.vc.is_playing():
                    state.vc.stop()
                    state.is_speaking = False

            # Hum after random 45-90s of silence
            silence_time = time.monotonic() - last_activity
            hum_cooldown = max(0, hum_cooldown - 0.15)
            if silence_time > random.uniform(999999, 999999) and hum_cooldown <= 0 and not state.vc.is_playing():
                earworm_file = earworm.get_random_hummed_earworm() or earworm.get_random_earworm()
                hum_file = earworm_file or (random.choice(HUM_FILES) if HUM_FILES else None)
                if not hum_file:
                    continue
                if state.vc and state.vc.is_connected():
                    state.is_speaking = True
                    done = asyncio.Event()
                    source = discord.PCMVolumeTransformer(
                        discord.FFmpegPCMAudio(str(hum_file), before_options="-nostdin", options="-vn"),
                        volume=0.3,
                    )
                    state.vc.play(
                        source,
                        after=lambda e: self.bot.loop.call_soon_threadsafe(done.set),
                    )
                    # Wait but stop if someone speaks
                    while not done.is_set():
                        await asyncio.sleep(0.2)
                        anyone_now = any(buf.chunks and buf.silence_duration() < 0.3 for buf in state.buffers.values())
                        if anyone_now and state.vc.is_playing():
                            state.vc.stop()
                            break
                    state.is_speaking = False
                    last_activity = time.monotonic()
                    hum_cooldown = 180  # Don't hum again for 10 minutes

            for user_id, buf in list(state.buffers.items()):
                if not buf.chunks:
                    continue
                if buf.silence_duration() < SILENCE_THRESHOLD:
                    continue
                if buf.duration() < MIN_AUDIO_LENGTH:
                    buf.clear()
                    continue

                # User stopped talking — process their audio
                dur = buf.duration()
                num_chunks = len(buf.chunks)
                total_bytes = sum(len(c) for c in buf.chunks)
                log.info(f"Processing {user_id}: {num_chunks} chunks, {total_bytes} bytes, {dur:.1f}s")
                wav_data = buf.to_wav()
                # Debug: save last audio for testing
                Path("/tmp/last_voice_input.wav").write_bytes(wav_data)
                log.info(f"WAV saved: {len(wav_data)} bytes to /tmp/last_voice_input.wav")
                buf.clear()

                # Get the user object
                guild = self.bot.get_guild(guild_id)
                member = guild.get_member(user_id) if guild else None
                user_name = member.display_name if member else "Someone"

                # Transcribe
                log.info(f"Transcribing {buf.duration():.1f}s audio from {user_name}")
                text = await _transcribe(wav_data)
                if not text:
                    log.info(f"No transcription result for {user_name}")
                    continue
                log.info(f"Transcribed from {user_name}: {text}")

                # Users who can talk without saying the trigger word
                ALWAYS_RESPOND_TO = {298000849794236416}  # D-Man

                # Only respond when called by name — unless whitelisted
                text_lower = text.lower()
                triggers = ("sarcastic bitch", "sarcastic b", "hey sb", "yo sb",
                            "okay sb", "ok sb", " sb ", " sb,", " sb?", " sb!",
                            "cast a bitch", "astic bitch", "hey s b", "s.b.",
                            "s b ", "hey bitch", "yo bitch", "astic b",
                            "sb.", "esbe", "a sb", "bitch")
                first_word = text_lower.split()[0] if text_lower.split() else ""
                sb_sounds = ("sb", "esb", "sp", "as")
                starts_like_sb = first_word in sb_sounds or text_lower.startswith("sb")
                # Whitelisted users: respond only to questions (not every statement)
                is_question = any(text_lower.rstrip().endswith(q) for q in ("?", "right", "huh", "eh"))
                is_question = is_question or any(text_lower.startswith(w) for w in (
                    "what", "who", "where", "when", "why", "how",
                    "can you", "could you", "do you", "are you", "is it",
                    "will you", "would you", "should", "does", "did",
                    "have you", "tell me", "explain",
                ))
                whitelisted = user_id in ALWAYS_RESPOND_TO and is_question
                if not whitelisted and not any(t in text_lower for t in triggers) and not starts_like_sb:
                    # Still remember what they said for context
                    state.history.append({"role": "user", "content": f"{user_name}: {text}"})
                    if len(state.history) > 30:
                        state.history[:] = state.history[-20:]
                    memory.remember_user(user_name, f"Said in voice: {text[:100]}")
                    continue

                # Get LLM reply
                reply = await _llm_reply(state.history, user_name, text)

                # Speak the reply
                state.is_speaking = True
                try:
                    log.info(f"Generating TTS for: {reply[:50]}")
                    audio = await _tts(reply)
                    if audio and state.vc and state.vc.is_connected():
                        # is_speaking flag already set — callback ignores incoming audio

                        # Save to temp WAV for reliable playback
                        tmp = f"/tmp/voice_reply_{guild_id}.mp3"
                        with open(tmp, "wb") as f:
                            f.write(audio)
                        log.info(f"TTS audio: {len(audio)} bytes, playing...")

                        source = discord.FFmpegPCMAudio(
                            tmp,
                            before_options="-nostdin",
                            options="-vn",
                        )

                        # Use an event to wait for playback completion
                        done = asyncio.Event()

                        def after_play(error):
                            if error:
                                log.error(f"Playback error: {error}")
                            done.set()

                        state.vc.play(source, after=lambda e: self.bot.loop.call_soon_threadsafe(done.set))

                        # Wait for playback to finish (max 30s)
                        try:
                            await asyncio.wait_for(done.wait(), timeout=30)
                        except asyncio.TimeoutError:
                            log.warning("Playback timed out")
                            if state.vc.is_playing():
                                state.vc.stop()

                        log.info("Playback finished")

                        # Listening continues — is_speaking flag handles filtering

                        # Voice only — no text channel echo
                    else:
                        log.warning("TTS failed or VC disconnected")
                except Exception as e:
                    log.error(f"Voice reply error: {e}")
                    if state.text_channel:
                        await state.text_channel.send(f"*voice broke: {e}*")
                finally:
                    state.is_speaking = False

    async def _auto_join(self, channel: discord.VoiceChannel, guild: discord.Guild):
        """Auto-join a voice channel and start lurking."""
        state = _states[guild.id]
        if state.vc and state.vc.is_connected():
            return  # Already in a channel

        try:
            state.vc = await channel.connect(cls=VoiceRecvClient)
            state.active = True
            state.history.clear()

            # Find a text channel to log to
            for tc in guild.text_channels:
                if tc.permissions_for(guild.me).send_messages:
                    state.text_channel = tc
                    break

            # Set up audio callback
            def on_audio(user, data):
                if not state.active or state.is_speaking:
                    return
                if user and not user.bot:
                    state.buffers[user.id].add(data.pcm)

            _pkt_count = [0]
            state.vc.listen(BasicSink(on_audio))
            log.info(f"Auto-joined and listening in {channel.name} ({guild.name})")

            # Start polling
            task = asyncio.create_task(self._poll_silence(guild.id))
            self._poll_tasks[guild.id] = task
        except Exception:
            pass

    async def _auto_leave(self, guild: discord.Guild):
        """Leave voice when empty."""
        state = _states[guild.id]
        state.active = False
        if guild.id in self._poll_tasks:
            self._poll_tasks[guild.id].cancel()
            del self._poll_tasks[guild.id]
        if state.vc and state.vc.is_connected():
            state.vc.stop_listening()
            await state.vc.disconnect()
            state.vc = None
        state.buffers.clear()

    def _is_dad(self, member: discord.Member) -> bool:
        """Check if a member is Dirty (dad)."""
        name = member.display_name.lower()
        username = member.name.lower() if member.name else ""
        dad_names = ("dirty", "dirty d", "dir7y", "crack spider's bitch", "crack spider's bitch (d-man)", "d-man")
        return any(d in name for d in dad_names) or any(d in username for d in dad_names)

    def _find_dad_channel(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        """Find the voice channel Dirty is in — always priority."""
        for vc in guild.voice_channels:
            for m in vc.members:
                if not m.bot and self._is_dad(m):
                    return vc
        return None

    def _find_best_channel(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        """Find best channel: dad's channel first, then most populated."""
        dad_ch = self._find_dad_channel(guild)
        if dad_ch:
            return dad_ch
        best_channel = None
        best_count = 0
        for vc in guild.voice_channels:
            real = [m for m in vc.members if not m.bot]
            if len(real) > best_count:
                best_count = len(real)
                best_channel = vc
        return best_channel

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Auto-join, follow dad, auto-leave when empty."""
        if member.bot:
            return

        guild = member.guild
        state = _states[guild.id]

        # DAD PRIORITY — if Dirty joins or moves, follow him immediately
        if self._is_dad(member) and after.channel:
            if state.vc and state.vc.is_connected() and state.vc.channel != after.channel:
                log.info(f"Dad (Dirty) moved to {after.channel.name} — following!")
                await self._auto_leave(guild)
                await asyncio.sleep(1)
                await self._auto_join(after.channel, guild)
                return
            elif not state.vc or not state.vc.is_connected():
                log.info(f"Dad (Dirty) is in {after.channel.name} — joining!")
                await asyncio.sleep(1)
                await self._auto_join(after.channel, guild)
                return

        # Someone joined a voice channel — auto-join the best one
        if after.channel and not before.channel:
            if not state.vc or not state.vc.is_connected():
                await asyncio.sleep(2)
                best = self._find_best_channel(guild)
                if best:
                    log.info(f"Auto-joining {best.name}")
                    await self._auto_join(best, guild)

        # Someone moved channels — follow if we'd be alone
        if before.channel and after.channel and before.channel != after.channel:
            if state.vc and state.vc.is_connected() and state.vc.channel == before.channel:
                real_members = [m for m in before.channel.members if not m.bot]
                if len(real_members) == 0:
                    await self._auto_leave(guild)
                    await asyncio.sleep(1)
                    best = self._find_best_channel(guild)
                    if best:
                        await self._auto_join(best, guild)

        # Someone left — check if we should leave or move
        if before.channel and (not after.channel or after.channel != before.channel):
            if state.vc and state.vc.is_connected() and state.vc.channel == before.channel:
                real_members = [m for m in before.channel.members if not m.bot]
                if len(real_members) == 0:
                    # Check if dad is elsewhere
                    best = self._find_best_channel(guild)
                    if best:
                        await self._auto_leave(guild)
                        await asyncio.sleep(1)
                        await self._auto_join(best, guild)
                    else:
                        await self._auto_leave(guild)

    @commands.Cog.listener()
    async def on_ready(self):
        """On startup, auto-join dad's channel or most populated."""
        await asyncio.sleep(5)
        for guild in self.bot.guilds:
            best = self._find_best_channel(guild)
            if best:
                reason = "dad's channel" if self._find_dad_channel(guild) else "most populated"
                log.info(f"Startup: joining {best.name} ({reason}) in {guild.name}")
                await self._auto_join(best, guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceChatCog(bot))
