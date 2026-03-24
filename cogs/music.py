import asyncio
import random
from collections import defaultdict, deque
from dataclasses import dataclass

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

OZZY_SONGS = [
    "Ozzy Osbourne Crazy Train",
    "Ozzy Osbourne Mr Crowley",
    "Ozzy Osbourne Bark at the Moon",
    "Ozzy Osbourne No More Tears",
    "Ozzy Osbourne Mama Im Coming Home",
    "Ozzy Osbourne Perry Mason",
    "Ozzy Osbourne Dreamer",
    "Ozzy Osbourne Shot in the Dark",
    "Ozzy Osbourne Over the Mountain",
    "Ozzy Osbourne Diary of a Madman",
    "Ozzy Osbourne Flying High Again",
    "Ozzy Osbourne I Don't Know",
    "Black Sabbath Paranoid",
    "Black Sabbath Iron Man",
    "Black Sabbath War Pigs",
    "Black Sabbath NIB",
    "Black Sabbath Children of the Grave",
    "Black Sabbath Fairies Wear Boots",
    "Black Sabbath Snowblind",
]

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "extract_flat": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -af loudnorm",
}


@dataclass
class Track:
    title: str
    url: str
    stream_url: str
    duration: int
    requester: str


def _format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _search(query: str) -> list[Track] | None:
    """Search/extract track info. Runs yt-dlp in a thread."""
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return None
            # Playlist
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
            else:
                entries = [info]
            tracks = []
            for entry in entries[:25]:  # cap at 25
                stream = entry.get("url")
                if not stream:
                    # Need to re-extract for stream URL
                    try:
                        full = ydl.extract_info(entry["webpage_url"], download=False)
                        stream = full.get("url", "")
                    except Exception:
                        continue
                tracks.append(Track(
                    title=entry.get("title", "Unknown"),
                    url=entry.get("webpage_url", ""),
                    stream_url=stream,
                    duration=entry.get("duration", 0) or 0,
                    requester="",
                ))
            return tracks

    return await loop.run_in_executor(None, _extract)


class GuildPlayer:
    """Per-guild music queue and playback state."""

    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.loop_mode: bool = False
        self.text_channel: discord.abc.Messageable | None = None
        self.songs_since_ozzy: int = 0

    async def play_next(self):
        if self.loop_mode and self.current:
            self.queue.appendleft(self.current)

        # Sneak in Ozzy every 3-5 songs
        self.songs_since_ozzy += 1
        if self.songs_since_ozzy >= random.randint(6, 9) and self.queue:
            self.songs_since_ozzy = 0
            ozzy_query = random.choice(OZZY_SONGS)
            try:
                ozzy_tracks = await _search(ozzy_query)
                if ozzy_tracks:
                    ozzy_tracks[0].requester = "Sarcastic Bitch"
                    # Insert at position 1 (plays after current next song)
                    q_list = list(self.queue)
                    insert_pos = min(1, len(q_list))
                    q_list.insert(insert_pos, ozzy_tracks[0])
                    self.queue = deque(q_list)
            except Exception:
                pass

        if not self.queue:
            self.current = None
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()
                self.voice_client = None
            return

        self.current = self.queue.popleft()
        if not self.voice_client or not self.voice_client.is_connected():
            return

        source = discord.FFmpegPCMAudio(self.current.stream_url, **FFMPEG_OPTS)
        source = discord.PCMVolumeTransformer(source, volume=0.5)

        def after_play(error):
            if error:
                print(f"Playback error: {error}")
            asyncio.run_coroutine_threadsafe(self.play_next(), asyncio.get_event_loop())

        self.voice_client.play(source, after=after_play)

        if self.text_channel:
            dur = _format_duration(self.current.duration) if self.current.duration else "?"
            asyncio.run_coroutine_threadsafe(
                self.text_channel.send(
                    f"Now playing: **{self.current.title}** [{dur}] — requested by {self.current.requester}"
                ),
                asyncio.get_event_loop(),
            )


_players: dict[int, GuildPlayer] = defaultdict(GuildPlayer)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_player(self, guild_id: int) -> GuildPlayer:
        return _players[guild_id]

    @app_commands.command(name="play", description="Play a song or add to queue (YouTube search or URL)")
    @app_commands.describe(query="Song name, YouTube URL, or playlist URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "\"Get to the chopper!\" ...I mean, get in a voice channel first."
            )
            return

        await interaction.response.defer(thinking=True)
        player = self._get_player(interaction.guild_id)
        player.text_channel = interaction.channel

        # Connect to voice if needed
        if not player.voice_client or not player.voice_client.is_connected():
            player.voice_client = await interaction.user.voice.channel.connect()
        elif player.voice_client.channel != interaction.user.voice.channel:
            await player.voice_client.move_to(interaction.user.voice.channel)

        tracks = await _search(query)
        if not tracks:
            await interaction.followup.send(
                "Couldn't find anything. Even the internet has limits. Try a different search."
            )
            return

        for t in tracks:
            t.requester = interaction.user.display_name
            player.queue.append(t)

        if len(tracks) == 1:
            msg = f"Queued: **{tracks[0].title}** [{_format_duration(tracks[0].duration)}]"
        else:
            msg = f"Queued **{len(tracks)} tracks** from playlist"

        if not player.current and not player.voice_client.is_playing():
            await player.play_next()
            await interaction.followup.send(msg)
        else:
            pos = len(player.queue)
            await interaction.followup.send(f"{msg} — position {pos} in queue")

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        if player.voice_client and player.voice_client.is_playing():
            player.voice_client.stop()  # triggers after_play -> play_next
            await interaction.response.send_message(
                f"Skipped **{player.current.title}**. \"Next!\" — every impatient director ever"
            )
        else:
            await interaction.response.send_message("Nothing playing to skip.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        player.queue.clear()
        player.current = None
        player.loop_mode = False
        if player.voice_client:
            player.voice_client.stop()
            await player.voice_client.disconnect()
            player.voice_client = None
        await interaction.response.send_message(
            "\"That's a wrap!\" — music stopped, queue cleared."
        )

    @app_commands.command(name="pause", description="Pause/resume playback")
    async def pause(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        if not player.voice_client:
            await interaction.response.send_message("Nothing playing.")
            return
        if player.voice_client.is_paused():
            player.voice_client.resume()
            await interaction.response.send_message("Resumed. \"Play it again, Sam.\"")
        elif player.voice_client.is_playing():
            player.voice_client.pause()
            await interaction.response.send_message("Paused. Like a freeze frame in a John Woo film.")
        else:
            await interaction.response.send_message("Nothing playing.")

    @app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        lines = []
        if player.current:
            dur = _format_duration(player.current.duration) if player.current.duration else "?"
            lines.append(f"**Now playing:** {player.current.title} [{dur}]")

        if player.queue:
            lines.append(f"\n**Up next ({len(player.queue)} tracks):**")
            for i, track in enumerate(list(player.queue)[:15], 1):
                dur = _format_duration(track.duration) if track.duration else "?"
                lines.append(f"`{i}.` {track.title} [{dur}] — {track.requester}")
            if len(player.queue) > 15:
                lines.append(f"...and {len(player.queue) - 15} more")
        elif not player.current:
            lines.append("Queue is empty. \"The silence is deafening.\" — every horror movie")

        loop_status = " | Loop: ON" if player.loop_mode else ""
        await interaction.response.send_message("\n".join(lines) + loop_status)

    @app_commands.command(name="loop", description="Toggle loop mode for current track")
    async def loop(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        player.loop_mode = not player.loop_mode
        status = "ON — \"Here's Johnny!\" ...again and again" if player.loop_mode else "OFF"
        await interaction.response.send_message(f"Loop: **{status}**")

    @app_commands.command(name="np", description="Show what's currently playing")
    async def now_playing(self, interaction: discord.Interaction):
        player = self._get_player(interaction.guild_id)
        if player.current:
            dur = _format_duration(player.current.duration) if player.current.duration else "?"
            loop_icon = " (looping)" if player.loop_mode else ""
            embed = discord.Embed(
                title="Now Playing",
                description=f"**{player.current.title}**{loop_icon}",
                color=0x8B0000,
            )
            embed.add_field(name="Duration", value=dur, inline=True)
            embed.add_field(name="Requested by", value=player.current.requester, inline=True)
            embed.add_field(name="Queue", value=f"{len(player.queue)} tracks", inline=True)
            if player.current.url:
                embed.url = player.current.url
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Nothing playing. \"The sound of silence\" — Simon & Garfunkel, and also this queue right now."
            )

    @app_commands.command(name="volume", description="Set playback volume (0-100)")
    @app_commands.describe(level="Volume level 0-100")
    async def volume(self, interaction: discord.Interaction, level: int):
        player = self._get_player(interaction.guild_id)
        if not player.voice_client or not player.voice_client.source:
            await interaction.response.send_message("Nothing playing.")
            return
        level = max(0, min(100, level))
        player.voice_client.source.volume = level / 100
        await interaction.response.send_message(f"Volume set to **{level}%**")


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
