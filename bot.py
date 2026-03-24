import suppress_rtcp_log
suppress_rtcp_log.apply()
import patch_voice
patch_voice.apply()

import asyncio
import discord
from discord.ext import commands
import config

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"{config.BOT_NAME} is online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(name="help", description="What can this bot do?")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"{config.BOT_NAME}",
        description=(
            "\"I'm gonna make you an offer you can't refuse.\" "
            "Actually, you don't get a choice. I'm already here.\n\n"
            "**Commands:**\n"
            "`/chat <message>` — Talk to me. Try to keep up.\n"
            "`/inspect <image>` — Send me a pic or screenshot and I'll tell you what I see.\n"
            "`/imagine <prompt>` — I paint pictures. Digital ones. Like Blade Runner but less existential.\n"
            "`/video <prompt>` — Coming soon. Even Kubrick needed time.\n"
            "`/clip <name>` — Play a meme audio clip in voice chat.\n"
            "`/clips` — List available meme clips.\n"
            "`/tts <text>` — Text-to-speech. I'll say it so you don't have to.\n"
            "`/upload_clip` — Add a new meme clip to the library.\n"
            "`/play <query>` — Play music from YouTube. Supports URLs and search.\n"
            "`/skip` `/stop` `/pause` `/queue` `/np` `/volume` `/loop` — Music controls.\n"
            "`/join` — Join voice chat for a live conversation. Say **\"SB\"** or **\"Sarcastic Bitch\"** to talk to me.\n"
            "`/leave` — Leave voice chat.\n\n"
            "Or just **@mention me** in any channel and I'll respond. "
            "Like that one friend who always has something to say."
        ),
        color=0x8B0000,
    )
    await interaction.response.send_message(embed=embed)


async def main():
    async with bot:
        await bot.load_extension("cogs.chat")
        await bot.load_extension("cogs.imagine")
        await bot.load_extension("cogs.video")
        await bot.load_extension("cogs.audio")
        await bot.load_extension("cogs.music")
        await bot.load_extension("cogs.voice_chat")
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
