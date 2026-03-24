import discord
from discord import app_commands
from discord.ext import commands


class VideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="video", description="Generate a short video clip. Coming soon."
    )
    @app_commands.describe(prompt="What kind of video do you want?")
    async def video(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.send_message(
            "\"Patience, grasshopper.\" — every kung fu movie ever.\n\n"
            "Video generation is coming soon. Even Spielberg needed post-production time. "
            "For now, try `/imagine` — still pictures worked fine for Kubrick in 2001."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoCog(bot))
