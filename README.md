# sar-bi-ai

A self-hosted Discord bot powered by local AI services. Combines LLM chat, image generation, text-to-speech, speech-to-text, music playback, and real-time voice conversation into a single bot with a customizable personality.

Built to run alongside a local AI server stack (LLM inference, ComfyUI, Kokoro TTS, Whisper STT) — no cloud API keys needed beyond Discord.

## Features

### Chat & Conversation
- `/chat <message>` — LLM-powered conversation via OpenAI-compatible API
- `@mention` the bot in any channel for a reply
- Per-channel conversation history (last 20 messages)
- Hot-reloadable system prompt for personality customization
- Automatic voice message transcription and reply (Whisper STT → LLM → TTS audio response)

### Image Analysis & Face Recognition
- `/inspect <image>` — Multimodal image analysis (describe, question, analyze screenshots)
- `/thisis @user <photo>` — Teach the bot to recognize someone's face
- `/whois <photo>` — Identify a person from a photo using stored references
- `/faces` — List all registered faces

### Image Generation
- `/imagine <prompt>` — Generate images via ComfyUI with FLUX.1-schnell
- 1024x1024 output, 4-step generation
- Results delivered as embedded images in Discord

### Music Player
- `/play <query>` — YouTube search or direct URL playback via yt-dlp
- Playlist support (up to 25 tracks)
- `/skip`, `/stop`, `/pause`, `/queue`, `/np`, `/volume`, `/loop` — Full playback controls
- Per-guild queue with FFmpeg audio streaming and loudness normalization

### Voice Chat (Real-Time Conversation)
- `/join` — Bot joins your voice channel and listens for speech
- Wake-word activated — say "SB" or the bot's name to trigger a response
- Full pipeline: live audio capture → Whisper transcription → LLM response → Kokoro TTS playback
- Auto-joins voice channels when users connect
- Auto-follows priority users between channels
- Silence detection with configurable thresholds
- `/say <text>` — Force the bot to speak something in voice
- `/leave` — Disconnect from voice

### Audio Clips & TTS
- `/clip <name>` — Play a meme audio clip in voice chat
- `/clips` — List available clips
- `/upload_clip` — Add new audio clips to the library (also adds to Discord soundboard)
- `/tts <text>` — Text-to-speech playback in voice or as an audio file attachment

### Memory System
- SQLite-backed persistent memory per user
- Tracks conversation history, topics of interest, and memorable moments
- Automatic topic extraction from messages
- Follow-up question queue for natural conversation continuity
- Memory context injected into LLM prompts for personalized responses

## Requirements

### Backend Services

The bot connects to these services over HTTP (default localhost addresses):

| Service | Default URL | Purpose |
|---------|-------------|---------|
| LLM Server | `http://127.0.0.1:11434/v1` | OpenAI-compatible chat completions (llama.cpp, Ollama, vLLM, etc.) |
| ComfyUI | `http://127.0.0.1:8188` | FLUX.1-schnell image generation |
| Kokoro TTS | `http://127.0.0.1:8880` | Text-to-speech (OpenAI-compatible `/v1/audio/speech`) |
| Whisper STT | `http://127.0.0.1:9000` | Speech-to-text (OpenAI-compatible `/v1/audio/transcriptions`) |

### Default Models

| Component | Default Model | Notes |
|-----------|--------------|-------|
| LLM | `dolphin-2.9.4-llama3.1-8b-Q4_K_M.gguf` | Uncensored Dolphin (Eric Hartford). No refusals, follows the system prompt personality without filtering. Any OpenAI-compatible model works — set via `LLM_MODEL` in `.env` |
| Image Gen | `flux1-schnell.safetensors` | FLUX.1-schnell (Black Forest Labs). Fast 4-step generation. Requires `ae.safetensors` VAE, `t5xxl_fp16.safetensors` and `clip_l.safetensors` CLIP models in ComfyUI |
| TTS | `kokoro` | Kokoro TTS with configurable voice (default `af_nova`). Set voice via `TTS_VOICE` in `.env` |
| STT (Chat) | `Systran/faster-whisper-base.en` | Faster-Whisper base model for text channel voice messages |
| STT (Voice) | `Systran/faster-whisper-tiny.en` | Faster-Whisper tiny model for real-time voice chat (lower latency) |

> **Why Dolphin?** The bot's personality is R-rated by design — it swears, roasts, flirts, and never breaks character. Standard censored models will constantly refuse or sanitize responses, breaking the experience. Dolphin is purpose-built for unrestricted instruction-following.

### System Dependencies

- **Python 3.11+**
- **FFmpeg** — required for music playback and voice audio processing

### Python Dependencies

```
discord.py>=2.4.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
openai>=1.30.0
PyNaCl>=1.5.0
yt-dlp>=2024.0.0
discord-ext-voice-recv>=0.5.0
```

### Discord Bot Setup

1. Create a bot at the [Discord Developer Portal](https://discord.com/developers/applications)
2. Enable these **Privileged Gateway Intents**:
   - Message Content Intent
   - Server Members Intent
3. Invite with permissions: Send Messages, Connect, Speak, Attach Files, Use Slash Commands, Manage Guild (optional, for soundboard)

## Setup

```bash
# Clone
git clone https://github.com/bong-water-water-bong/sar-bi-ai.git
cd sar-bi-ai

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your Discord token and service URLs

# Run
python bot.py
```

## Configuration

All configuration is done through environment variables in `.env`:

```env
DISCORD_TOKEN=your-discord-bot-token-here
LLM_API_URL=http://127.0.0.1:11434/v1
LLM_MODEL=dolphin-2.9.4-llama3.1-8b-Q4_K_M.gguf
COMFYUI_URL=http://127.0.0.1:8188
BOT_NAME=Reaper
TTS_VOICE=af_nova
```

The bot's personality is defined in `system_prompt.txt` and hot-reloads when modified — no restart needed.

## Architecture

```
bot.py              — Entry point, loads cogs, registers /help
config.py           — Environment config loader
memory.py           — SQLite-backed per-user memory system
faces.py            — Face recognition registry (LLM vision-based)
system_prompt.txt   — Bot personality definition (hot-reloadable)
cogs/
  chat.py           — /chat, /inspect, /thisis, /whois, @mention handler
  imagine.py        — /imagine (ComfyUI FLUX integration)
  music.py          — /play, /skip, /stop, /pause, /queue, /np, /volume, /loop
  voice_chat.py     — /join, /leave, /say, real-time voice conversation
  audio.py          — /clip, /clips, /tts, /upload_clip
  video.py          — /video (placeholder, coming soon)
```
