"""
Leaderboard Cog
===============
Tracks message counts per user per guild and exposes them via
/leaderboard (top 10) and /user_stats (specific user).

Time periods (selectable via dropdown):
  - All time
  - Yearly (last 365 days)
  - Monthly (last 30 days)
  - Weekly (last 7 days)
  - Daily (today only, UTC)

CHANGELOG:
- [NEW] Tracks every guild message in SQLite (bots/webhooks/DMs excluded)
- [NEW] /leaderboard — dropdown-based top 10 leaderboard
- [NEW] /user_stats — dropdown-based user lookup with rank
- [PERF] All-time uses cached counter (fast reads)
- [PERF] Period stats use indexed message_log table
- [SAFETY] DB write errors never break the message flow
- [UX] Auto-deferred response (command takes ~500ms to query)
- [PERM] Open to all members (removed admin restriction)
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

import database

PRIMARY = 0x8b5cf6
SUCCESS = 0x10b981
ERROR = 0xef4444
WARN = 0xf59e0b

PERIODS = {
    "all_time": {"label": "All Time", "days": None,    "emoji": "🏆"},
    "yearly":   {"label": "Yearly",   "days": 365,     "emoji": "📅"},
    "monthly":  {"label": "Monthly",  "days": 30,      "emoji": "🗓️"},
    "weekly":   {"label": "Weekly",   "days": 7,       "emoji": "📆"},
    "daily":    {"label": "Daily",    "days": 1,       "emoji": "☀️"},
}


# ── Dropdown View ────────────────────────────────────────────────────────────

class PeriodSelectView(discord.ui.View):
    """Persistent dropdown that lets users pick a time period."""

    def __init__(self, cog: "LeaderboardCog", guild_id: int, target_user: Optional[discord.User] = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.target_user = target_user
        self.selected: Optional[str] = None
        self.message: Optional[discord.Message] = None

        options = []
        for key, info in PERIODS.items():
            options.append(discord.SelectOption(
                label=info["label"],
                value=key,
                emoji=info["emoji"],
            ))
        self.select = discord.ui.Select(
            placeholder="Select a time period…",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.guild_id != self.guild_id:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        self.selected = self.select.values[0]
        await interaction.response.defer()
        embed = await self.cog._build_embed(self.guild_id, self.selected, self.target_user)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


# ── Cog ─────────────────────────────────────────────────────────────────────

class LeaderboardCog(commands.Cog):
    """Tracks message activity and exposes leaderboard commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Message tracking ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Increment counters for every valid guild message."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.webhook_id is not None:
            return
        if message.type != discord.MessageType.default and message.type != discord.MessageType.reply:
            return
        if message.content.startswith("/") or message.content.startswith("!"):
            return

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                database.increment_message_count,
                message.guild.id,
                message.author.id,
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """We intentionally do nothing here — only count new messages."""
        return

    # ── Commands ──────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="leaderboard",
        description="Show the top message senders in this server.",
        usage="leaderboard",
    )
    async def leaderboard(self, ctx: commands.Context):
        if not ctx.guild:
            return await self._reply(ctx, "❌ Server-only command.", ephemeral=True)

        if ctx.interaction:
            await ctx.interaction.response.defer()

        embed = await self._build_embed(ctx.guild.id, "all_time", None)
        view = PeriodSelectView(self, ctx.guild.id)
        view.message = await ctx.channel.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="user_stats",
        description="Show how many messages a specific user has sent.",
        usage="user_stats @user",
    )
    async def user_stats(
        self,
        ctx: commands.Context,
        user: discord.Member,
    ):
        if not ctx.guild:
            return await self._reply(ctx, "❌ Server-only command.", ephemeral=True)
        if user.bot:
            return await self._reply(ctx, "❌ Bots don't have stats.", ephemeral=True)

        if ctx.interaction:
            await ctx.interaction.response.defer()

        embed = await self._build_embed(ctx.guild.id, "all_time", user)
        view = PeriodSelectView(self, ctx.guild.id, target_user=user)
        view.message = await ctx.channel.send(embed=embed, view=view)

    # ── Error handlers ───────────────────────────────────────────────────

    @leaderboard.error
    async def leaderboard_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await self._reply(ctx, "❌ Please check your command.", ephemeral=True)
        elif isinstance(error, commands.CommandOnCooldown):
            await self._reply(ctx, f"⏱️ Cooldown: {error.retry_after:.1f}s remaining.", ephemeral=True)

    @user_stats.error
    async def user_stats_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await self._reply(ctx, "❌ Please mention a valid member.", ephemeral=True)
        elif isinstance(error, commands.CommandOnCooldown):
            await self._reply(ctx, f"⏱️ Cooldown: {error.retry_after:.1f}s remaining.", ephemeral=True)

    # ── Embed builder ─────────────────────────────────────────────────────

    async def _build_embed(
        self,
        guild_id: int,
        period: str,
        target_user: Optional[discord.User],
    ) -> discord.Embed:
        info = PERIODS.get(period, PERIODS["all_time"])
        guild = self.bot.get_guild(guild_id)

        loop = asyncio.get_event_loop()

        if target_user:
            if info["days"] is None:
                count = await loop.run_in_executor(
                    None, database.get_user_all_time_count, guild_id, target_user.id
                )
                rank = await loop.run_in_executor(
                    None, database.get_user_rank, guild_id, target_user.id
                )
                period_label = "all time"
            else:
                count = await loop.run_in_executor(
                    None, database.get_user_period_count, guild_id, target_user.id, info["days"]
                )
                rank = await self._rank_in_period(guild_id, target_user.id, info["days"])

                if period == "daily":
                    period_label = "today"
                elif period == "weekly":
                    period_label = "this week"
                elif period == "monthly":
                    period_label = "this month"
                elif period == "yearly":
                    period_label = "this year"
                else:
                    period_label = info["label"].lower()

            embed = discord.Embed(
                title=f"📊 {target_user.display_name}'s Stats",
                color=PRIMARY,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.add_field(
                name="Messages",
                value=f"`{count:,}` {period_label}",
                inline=True,
            )
            embed.add_field(
                name="Rank",
                value=f"`#{rank}`" if rank > 0 else "Not ranked",
                inline=True,
            )
            if guild and guild.icon:
                embed.set_footer(text=guild.name, icon_url=guild.icon.url)
            else:
                embed.set_footer(text="TLC-Bot Leaderboard")
            return embed

        # Leaderboard view (top 10)
        if info["days"] is None:
            rows = await loop.run_in_executor(
                None, database.get_all_time_leaderboard, guild_id, 10
            )
            period_label = "all time"
        else:
            rows = await loop.run_in_executor(
                None, database.get_period_leaderboard, guild_id, info["days"], 10
            )
            if period == "daily":
                period_label = "today"
            elif period == "weekly":
                period_label = "this week"
            elif period == "monthly":
                period_label = "this month"
            elif period == "yearly":
                period_label = "this year"
            else:
                period_label = info["label"].lower()

        embed = discord.Embed(
            title=f"🏆 Message Leaderboard — {info['label']}",
            description=f"Top 10 chatters {period_label}",
            color=PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            embed.add_field(
                name="No data yet",
                value="Once people start chatting, rankings will appear here.",
                inline=False,
            )
            return embed

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, (user_id, count) in enumerate(rows, start=1):
            medal = medals.get(i, f"`#{i:>2}`")
            member = guild.get_member(user_id) if guild else None
            display = member.display_name if member else f"User {user_id}"
            lines.append(f"{medal} **{display}** — `{count:,}` messages")

        embed.description = "\n".join(lines)

        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        else:
            embed.set_footer(text="TLC-Bot Leaderboard")

        return embed

    async def _rank_in_period(self, guild_id: int, user_id: int, days: int) -> int:
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, database.get_period_leaderboard, guild_id, days, 1000
        )
        for i, (uid, _) in enumerate(rows, start=1):
            if uid == user_id:
                return i
        return 0

    async def _reply(self, ctx, content=None, embed=None, ephemeral=True):
        try:
            if ctx.interaction:
                if ctx.interaction.response.is_done():
                    return await ctx.interaction.followup.send(
                        content=content, embed=embed, ephemeral=ephemeral
                    )
                return await ctx.interaction.response.send_message(
                    content=content, embed=embed, ephemeral=ephemeral
                )
            return await ctx.reply(content=content, embed=embed)
        except Exception:
            try:
                return await ctx.send(content=content, embed=embed)
            except Exception:
                return None


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))
