import discord
from discord.ext import commands
from collections import deque
import time
import asyncio
from datetime import datetime, timedelta
import logging

from cogs.security_utils import (
    get_guild_security_config,
    save_guild_security_config,
    is_whitelisted,
    log_security_event
)

logger = logging.getLogger("TLCBot.AntiRaid")

class AntiRaidCog(commands.Cog):
    """Monitors join bursts, calculates account risk scores, and engages automatic lockdowns on high-risk raids."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Join tracker: { guild_id: deque([(timestamp, member_object)...]) }
        self.join_trackers = {}
        self.cooldowns = set()

    def get_guild_tracker(self, guild_id: int) -> deque:
        if guild_id not in self.join_trackers:
            self.join_trackers[guild_id] = deque(maxlen=100)
        return self.join_trackers[guild_id]

    def calculate_risk_score(self, member: discord.Member, join_velocity: int, cfg: dict) -> int:
        """Calculates a multi-signal risk score between 0 and 100."""
        score = 0
        now = datetime.utcnow()

        # Signal 1: Account Age
        account_age_days = (now - member.created_at.replace(tzinfo=None)).days
        min_age = cfg["anti_raid"].get("min_account_age_days", 3)
        
        if account_age_days == 0:
            score += 40
        elif account_age_days < min_age:
            score += 25

        # Signal 2: Default Avatar / Missing Profile Customizations
        if member.avatar is None:
            score += 15

        # Signal 3: Join Velocity Burst
        limit = cfg["anti_raid"]["join_limit"]
        if join_velocity >= limit * 2:
            score += 35
        elif join_velocity >= limit:
            score += 20

        return min(score, 100)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        cfg = get_guild_security_config(guild.id)

        if not cfg.get("enabled") or not cfg["anti_raid"]["enabled"]:
            return

        if is_whitelisted(member, cfg):
            return

        raid_cfg = cfg["anti_raid"]
        now = time.time()
        tracker = self.get_guild_tracker(guild.id)
        tracker.append((now, member))

        # Filter Window
        window = raid_cfg["join_window"]
        recent_joins = [j for j in tracker if j[0] >= now - window]
        join_count = len(recent_joins)

        # Calculate Individual Risk Score
        risk_score = self.calculate_risk_score(member, join_count, cfg)

        # Check Threshold
        if join_count >= raid_cfg["join_limit"]:
            await self.handle_raid_event(guild, member, join_count, window, risk_score, raid_cfg)

    async def handle_raid_event(self, guild: discord.Guild, trigger_member: discord.Member, join_count: int, window: int, risk_score: int, raid_cfg: dict):
        cd_key = guild.id
        if cd_key in self.cooldowns:
            return
        self.cooldowns.add(cd_key)

        action = raid_cfg["action"].lower()
        critical_score = raid_cfg.get("critical_risk_score", 75)

        risk_level = "LOW"
        if risk_score >= 80:
            risk_level = "CRITICAL"
        elif risk_score >= 60:
            risk_level = "HIGH"
        elif risk_score >= 40:
            risk_level = "MEDIUM"

        action_result = "Logged Only"

        # Auto-Lockdown on High Risk
        if (action == "lockdown" or risk_score >= critical_score) and raid_cfg.get("auto_lockdown_on_high_risk"):
            cfg = get_guild_security_config(guild.id)
            if not cfg.get("lockdown"):
                cfg["lockdown"] = True
                save_guild_security_config(guild.id, cfg)

                # Lock channels for @everyone
                for ch in guild.text_channels:
                    try:
                        ow = ch.overwrites_for(guild.default_role)
                        if ow.send_messages is not False:
                            ow.send_messages = False
                            await ch.set_permissions(guild.default_role, overwrite=ow, reason="Anti-Raid Lockdown Engaged")
                    except Exception:
                        pass
                action_result = "AUTOMATED LOCKDOWN ENGAGED"

        # Log Security Incident
        await log_security_event(
            guild,
            title="ANTI-RAID TRIGGERED",
            description=f"Rapid join burst detected in server.",
            fields=[
                ("Joins Detected", f"`{join_count}` joins / `{window}s`", True),
                ("Risk Level", f"`{risk_level}` ({risk_score}/100)", True),
                ("Trigger Account", f"{trigger_member.mention} (`{trigger_member.id}`)", True),
                ("Action Executed", action_result, True)
            ],
            color=discord.Color.red() if risk_score >= 60 else discord.Color.orange()
        )

        await asyncio.sleep(30)
        self.cooldowns.discard(cd_key)

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaidCog(bot))

