import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "dolphin-2.9.4-llama3.1-8b-Q4_K_M.gguf")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
BOT_NAME = os.getenv("BOT_NAME", "Reaper")
TTS_VOICE = os.getenv("TTS_VOICE", "af_nova")
MAX_HISTORY = 20
MAX_RESPONSE_LEN = 1900
