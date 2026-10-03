import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
from collections import defaultdict
import database as db

with open("config.json") as f:
    CONFIG = json.load(f)

SUCCESS = int(CONFIG["bot"]["success_color"])
ERROR   = int(CONFIG["bot"]["error_color"])
WARNING = int(CONFIG["bot"]["warning_color"])
PRIMARY = int(CONFIG["bot"]["color"])

SPAM_CFG   = CONFIG["security"]["anti_spam"]
RAID_CFG   = CONFIG["security"]["anti_raid"]

class Security(commands.Cog):
    """Anti-Spam and Anti-Raid for TLC Bot."""

    def __init__(self, bot):
        self.bot = bot
        self._spam_tracker: dict = defaultdict(lambda: defaultdict(list))
        self._join_tracker: dict = defaultdict(list)
        self._lockdown_guilds: set = set()
        self.cleanup_spam.start()

    def cog_unload(self):
        self.cleanup_spam.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        # Ensure prefix commands are processed across cogs!
        await self.bot.process_commands(message)

        if not SPAM_CFG["enabled"]:
            return
        if message.author.guild_permissions.administrator:
            return

        gid = message.guild.id
        uid = message.author.id
        now = datetime.utcnow()
        window = SPAM_CFG["time_window_seconds"]
        max_msgs = SPAM_CFG["max_messages"]

        timestamps = self._spam_tracker[gid][uid]
        timestamps.append(now)
        timestamps[:] = [t for t in timestamps if (now - t).total_seconds() <= window]

        if len(timestamps) >= max_msgs:
            timestamps.clear()
            await self._punish_spam(message)

    async def _punish_spam(self, message: discord.Message):
        member = message.author
        guild  = message.guild
        reason = "Auto-Mod: Spam detected"

        db.log_security_event(guild.id, "SPAM_DETECTED", member.id,
                              f"#{message.channel.name}", severity="medium")

        punishment = SPAM_CFG["punishment"]
        if punishment == "mute":
            duration = SPAM_CFG["mute_duration_minutes"]
            until    = datetime.utcnow() + timedelta(minutes=duration)
            try:
                await member.timeout(until, reason=reason)
            except Exception:
                pass
            action_text = f"Muted for {duration} minutes"
        elif punishment == "kick":
            try:
                await member.kick(reason=reason)
            except Exception:
                pass
            action_text = "Kicked"
        elif punishment == "ban":
            try:
                await member.ban(reason=reason)
            except Exception:
                pass
            action_text = "Banned"
        else:
            action_text = "Warning issued"

        try:
            await message.channel.send(embed=discord.Embed(
                title="🛡️ Anti-Spam Triggered",
                description=f"{member.mention} was detected spamming.\n**Action:** {action_text}",
                color=WARNING
            ), delete_after=8)
        except Exception:
            pass

        settings = db.get_guild_settings(guild.id)
        if settings and settings.get("mod_log_id"):
            ch = guild.get_channel(settings["mod_log_id"])
            if ch:
                embed = discord.Embed(
                    title="🛡️ Anti-Spam | Auto-Mod",
                    color=WARNING,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Member", value=f"{member} ({member.id})", inline=True)
                embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                embed.add_field(name="Action", value=action_text, inline=True)
                embed.set_footer(text="TLC Bot • Security")
                await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not RAID_CFG["enabled"]:
            return

        gid = member.guild.id
        now = datetime.utcnow()
        window = RAID_CFG["join_window_seconds"]
        threshold = RAID_CFG["join_threshold"]

        joins = self._join_tracker[gid]
        joins.append(now)
        joins[:] = [t for t in joins if (now - t).total_seconds() <= window]

        account_age = (now - member.created_at.replace(tzinfo=None)).days
        min_age = RAID_CFG["new_account_age_days"]
        if account_age < min_age:
            db.log_security_event(gid, "NEW_ACCOUNT_JOIN", member.id,
                                  f"Account age: {account_age}d", severity="low")

        if len(joins) >= threshold and gid not in self._lockdown_guilds:
            self._lockdown_guilds.add(gid)
            await self._trigger_raid_lockdown(member.guild)

    async def _trigger_raid_lockdown(self, guild: discord.Guild):
        db.log_security_event(guild.id, "RAID_DETECTED", None, "Auto-lockdown triggered", severity="critical")
        db.upsert_guild_settings(guild.id, lockdown_active=1)

        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Anti-Raid Lockdown")
            except Exception:
                pass

        settings = db.get_guild_settings(guild.id)
        alert_ch = None
        if settings and settings.get("alert_channel"):
            alert_ch = guild.get_channel(settings["alert_channel"])
        if not alert_ch:
            alert_ch = guild.system_channel

        if alert_ch:
            embed = discord.Embed(
                title="🚨 RAID DETECTED — SERVER LOCKED",
                description=(
                    "An unusual number of members joined in a short time.\n"
                    "The server has been **automatically locked down**.\n\n"
                    "Use `/raidmode off` or `?raidmode off` to unlock the server."
                ),
                color=ERROR,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="TLC Bot • Anti-Raid System")
            await alert_ch.send(embed=embed)

    @commands.hybrid_command(name="raidmode", description="Manually enable or disable raid lockdown.")
    @commands.has_permissions(administrator=True)
    async def raidmode(self, ctx: commands.Context, mode: str):
        mode = mode.lower()
        guild = ctx.guild

        if mode == "on":
            self._lockdown_guilds.add(guild.id)
            await self._trigger_raid_lockdown(guild)
            embed = discord.Embed(title="🔒 Raid Mode Enabled", description="Server is now in lockdown.", color=ERROR)
        elif mode == "off":
            self._lockdown_guilds.discard(guild.id)
            db.upsert_guild_settings(guild.id, lockdown_active=0)
            for channel in guild.text_channels:
                try:
                    overwrite = channel.overwrites_for(guild.default_role)
                    overwrite.send_messages = None
                    await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Raid mode disabled")
                except Exception:
                    pass
            embed = discord.Embed(title="🔓 Raid Mode Disabled", description="Server lockdown has been lifted.", color=SUCCESS)
        else:
            embed = discord.Embed(title="❌ Invalid", description="Use `on` or `off`.", color=ERROR)

        embed.set_footer(text="TLC Bot • Security")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="securitystatus", description="View the current security status of the server.")
    @commands.has_permissions(administrator=True)
    async def securitystatus(self, ctx: commands.Context):
        events = db.get_security_events(ctx.guild.id, limit=5)
        settings = db.get_guild_settings(ctx.guild.id) or {}

        embed = discord.Embed(
            title=f"🛡️ Security Status — {ctx.guild.name}",
            color=PRIMARY,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Anti-Spam",   value="✅ Enabled" if SPAM_CFG["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Anti-Raid",   value="✅ Enabled" if RAID_CFG["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Lockdown",    value="🔒 Active" if settings.get("lockdown_active") else "🔓 Inactive", inline=True)

        if events:
            recent = "\n".join(
                f"`{e['event_type']}` — {e['logged_at'][:16]} [{e['severity'].upper()}]"
                for e in events
            )
            embed.add_field(name="Recent Security Events", value=recent, inline=False)
        else:
            embed.add_field(name="Recent Security Events", value="No events logged.", inline=False)

        embed.set_footer(text="TLC Bot • Security")
        await ctx.send(embed=embed, ephemeral=True)

    @tasks.loop(minutes=5)
    async def cleanup_spam(self):
        now = datetime.utcnow()
        window = SPAM_CFG["time_window_seconds"]
        for gid in list(self._spam_tracker.keys()):
            for uid in list(self._spam_tracker[gid].keys()):
                self._spam_tracker[gid][uid] = [
                    t for t in self._spam_tracker[gid][uid]
                    if (now - t).total_seconds() <= window
                ]

    @cleanup_spam.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Security(bot))
