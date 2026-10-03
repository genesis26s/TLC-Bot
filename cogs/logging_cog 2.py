import discord
from discord.ext import commands
import json
from datetime import datetime
from typing import Optional
import database as db

with open("config.json") as f:
    CONFIG = json.load(f)

PRIMARY = int(CONFIG["bot"]["color"])
WARNING = int(CONFIG["bot"]["warning_color"])
ERROR   = int(CONFIG["bot"]["error_color"])
SUCCESS = int(CONFIG["bot"]["success_color"])

class LoggingCog(commands.Cog):
    """Full server event audit logger for TLC Bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Fetches configured log channel for the guild."""
        settings = db.get_guild_settings(guild.id)
        if not settings or not settings.get("mod_log_id"):
            return None
        return guild.get_channel(settings["mod_log_id"])

    # ── Hybrid Log Channel Configuration Command ─────────────────────────────
    @commands.hybrid_command(name="setlogchannel", description="Set the channel for server audit logs.")
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        db.upsert_guild_settings(ctx.guild.id, mod_log_id=channel.id)
        embed = discord.Embed(
            title="✅ Audit Log Channel Configured",
            description=f"Server audit logs will now be posted to {channel.mention}.",
            color=SUCCESS,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="TLC Bot • Logging System")
        await ctx.send(embed=embed, ephemeral=True)

    # ── Message Delete Audit Event ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        log_ch = await self.get_log_channel(message.guild)
        if not log_ch or log_ch.id == message.channel.id:
            return

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            color=ERROR,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Content", value=message.content if message.content else "*[No Text / Attachment Only]*", inline=False)
        embed.set_footer(text=f"TLC Bot • Channel ID: {message.channel.id}")

        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass

    # ── Message Edit Audit Event ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild or before.content == after.content:
            return

        log_ch = await self.get_log_channel(before.guild)
        if not log_ch or log_ch.id == before.channel.id:
            return

        embed = discord.Embed(
            title="✏️ Message Edited",
            color=WARNING,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Author", value=f"{before.author.mention} (`{before.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Before", value=before.content if before.content else "*[Empty]*", inline=False)
        embed.add_field(name="After", value=after.content if after.content else "*[Empty]*", inline=False)
        embed.set_footer(text=f"TLC Bot • Jump to: {after.jump_url}")

        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass

    # ── Member Join Audit Event ──────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        log_ch = await self.get_log_channel(member.guild)
        if not log_ch:
            return

        created_ago = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days

        embed = discord.Embed(
            title="📥 Member Joined",
            color=SUCCESS,
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member.mention} ({member.name})", inline=True)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Account Age", value=f"`{created_ago} days ago`", inline=True)
        embed.set_footer(text="TLC Bot • Member Audit")

        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass

    # ── Member Leave Audit Event ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        log_ch = await self.get_log_channel(member.guild)
        if not log_ch:
            return

        embed = discord.Embed(
            title="📤 Member Left",
            color=ERROR,
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Roles", value=f"`{len(member.roles) - 1}` roles", inline=True)
        embed.set_footer(text="TLC Bot • Member Audit")

        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass

    # ── Role Changes / Member Updates Audit Event ────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        log_ch = await self.get_log_channel(before.guild)
        if not log_ch:
            return

        # Check role additions/removals
        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]

            embed = discord.Embed(
                title="🎭 Roles Updated",
                color=PRIMARY,
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Member", value=after.mention, inline=True)
            if added:
                embed.add_field(name="Added Role(s)", value=" ".join(added), inline=False)
            if removed:
                embed.add_field(name="Removed Role(s)", value=" ".join(removed), inline=False)
            embed.set_footer(text=f"TLC Bot • User ID: {after.id}")

            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass

        # Check nickname changes
        if before.nick != after.nick:
            embed = discord.Embed(
                title="🏷️ Nickname Updated",
                color=PRIMARY,
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Member", value=after.mention, inline=True)
            embed.add_field(name="Before", value=before.nick or "*[None]*", inline=True)
            embed.add_field(name="After", value=after.nick or "*[None]*", inline=True)
            embed.set_footer(text=f"TLC Bot • User ID: {after.id}")

            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot))

