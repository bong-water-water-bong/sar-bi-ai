import io
import json
import uuid

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

FLUX_WORKFLOW = {
    "6": {
        "class_type": "EmptyLatentImage",
        "inputs": {"batch_size": 1, "height": 1024, "width": 1024},
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["13", 0], "vae": ["10", 0]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "discord", "images": ["8", 0]},
    },
    "10": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "ae.safetensors"},
    },
    "11": {
        "class_type": "DualCLIPLoader",
        "inputs": {
            "clip_name1": "t5xxl_fp16.safetensors",
            "clip_name2": "clip_l.safetensors",
            "type": "flux",
        },
    },
    "12": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "flux1-schnell.safetensors",
            "weight_dtype": "default",
        },
    },
    "13": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 1.0,
            "denoise": 1.0,
            "latent_image": ["6", 0],
            "model": ["12", 0],
            "negative": ["33", 0],
            "positive": ["22", 0],
            "sampler_name": "euler",
            "scheduler": "simple",
            "seed": -1,
            "steps": 4,
        },
    },
    "22": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["11", 0], "text": "PLACEHOLDER"},
    },
    "33": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["11", 0], "text": ""},
    },
}


async def _generate_image(prompt: str) -> bytes | None:
    """Queue a FLUX prompt on ComfyUI and return the PNG bytes."""
    client_id = str(uuid.uuid4())
    workflow = json.loads(json.dumps(FLUX_WORKFLOW))
    workflow["22"]["inputs"]["text"] = prompt
    workflow["13"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

    url = config.COMFYUI_URL
    async with aiohttp.ClientSession() as session:
        # Queue the prompt
        async with session.post(
            f"{url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            prompt_id = data["prompt_id"]

        # Poll for completion via history
        for _ in range(120):  # up to ~2 minutes
            async with session.get(f"{url}/history/{prompt_id}") as resp:
                history = await resp.json()
                if prompt_id in history:
                    break
            await __import__("asyncio").sleep(1)
        else:
            return None

        # Extract output filename
        outputs = history[prompt_id]["outputs"]
        for node_output in outputs.values():
            if "images" in node_output:
                img_info = node_output["images"][0]
                filename = img_info["filename"]
                subfolder = img_info.get("subfolder", "")
                # Fetch the image
                params = {"filename": filename, "subfolder": subfolder, "type": "output"}
                async with session.get(f"{url}/view", params=params) as resp:
                    if resp.status == 200:
                        return await resp.read()
    return None


class ImagineCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="imagine", description="Generate an image. Like Blade Runner, but you pick the scene."
    )
    @app_commands.describe(prompt="Describe what you want to see")
    async def imagine(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer(thinking=True)
        try:
            img_bytes = await _generate_image(prompt)
        except Exception as e:
            await interaction.followup.send(
                f"*projector malfunction* — image gen broke: `{e}`"
            )
            return

        if img_bytes:
            file = discord.File(io.BytesIO(img_bytes), filename="imagine.png")
            embed = discord.Embed(
                description=f"*\"{prompt}\"*",
                color=0x8B0000,
            )
            embed.set_image(url="attachment://imagine.png")
            embed.set_footer(text="FLUX.1-schnell | 4 steps")
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(
                "\"I've seen things you people wouldn't believe...\" "
                "but apparently not this image. Generation failed. Try again."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImagineCog(bot))
