import discord
from discord.ext import commands
from collections import defaultdict, deque
import time
import asyncio
from datetime import timedelta
import logging

from cogs.security_utils import (
    get_guild_security_config,
    is_whitelisted,
    can_moderate_user,
    log_security_event
)

logger = logging.getLogger("TLCBot.AntiNuke")

class AntiNukeCog(commands.Cog):
    """Monitors administrative destructive events (channel/role deletions, mass bans/kicks, webhooks)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Rolling window trackers: { (guild_id, user_id, action_type): deque([timestamps...]) }
        self.trackers = defaultdict(lambda: deque(maxlen=20))
        # Penalty cooldown to prevent repeated punishments for the same burst
        self.cooldowns = set()

    def record_and_check(self, guild_id: int, user_id: int, action_type: str, limit: int, window: int) -> bool:
        now = time.time()
        key = (guild_id, user_id, action_type)
        dq = self.trackers[key]
        dq.append(now)

        # Remove expired timestamps
        while dq and dq[0] < now - window:
            dq.popleft()

        return len(dq) >= limit

    async def identify_executor(self, guild: discord.Guild, audit_type: discord.AuditLogAction) -> discord.Member:
        """Inspects audit logs safely to verify the user responsible for an administrative action."""
        try:
            await asyncio.sleep(0.5)  # Slight delay for Discord API to process log entry
            async for entry in guild.audit_logs(limit=3, action=audit_type):
                # Verify entry is recent (within 10 seconds)
                if (discord.utils.utcnow() - entry.created_at).total_seconds() < 10:
                    if isinstance(entry.user, discord.Member):
                        return entry.user
                    elif isinstance(entry.user, discord.User):
                        return guild.get_member(entry.user.id)
        except Exception as e:
            logger.warning(f"Audit log fetch failed in {guild.id}: {e}")
        return None

    async def execute_antinuke_sanction(self, guild: discord.Guild, actor: discord.Member, trigger_reason: str, count: int, window: int):
        cd_key = (guild.id, actor.id, trigger_reason)
        if cd_key in self.cooldowns:
            return
        self.cooldowns.add(cd_key)

        cfg = get_guild_security_config(guild.id)
        action = cfg["anti_nuke"]["action"].lower()

        # Check Whitelist & Role Hierarchy Safety
        if is_whitelisted(actor, cfg) or not can_moderate_user(guild, guild.me, actor):
            await log_security_event(
                guild,
                title="ANTI-NUKE THRESHOLD EXCEEDED (SKIPPED)",
                description=f"Whitelisted or higher-hierarchy user {actor.mention} triggered `{trigger_reason}` threshold.",
                fields=[
                    ("Activity", f"`{count}` actions in `{window}s`", True),
                    ("Executor", actor.mention, True),
                    ("Status", "Skipped (Whitelisted/Owner)", True)
                ],
                color=discord.Color.yellow()
            )
            await asyncio.sleep(15)
            self.cooldowns.discard(cd_key)
            return

        result_str = "Successful"
        try:
            if action == "warn":
                try:
                    await actor.send(f"⚠️ **Security Alert:** You triggered Anti-Nuke detection in **{guild.name}** for `{trigger_reason}`.")
                except Exception:
                    pass
                result_str = "Warned via Direct Message"

            elif action == "timeout":
                await actor.timeout(timedelta(hours=1), reason=f"Anti-Nuke Triggered: {trigger_reason}")
                result_str = "Timed out for 1 Hour"

            elif action == "kick":
                await actor.kick(reason=f"Anti-Nuke Triggered: {trigger_reason}")
                result_str = "Kicked from Server"

            elif action == "ban":
                await actor.ban(reason=f"Anti-Nuke Triggered: {trigger_reason}", delete_message_days=0)
                result_str = "Permanently Banned"

            elif action == "log_only":
                result_str = "Logged Only (No Penalty Applied)"

        except Exception as e:
            result_str = f"Failed ({str(e)})"

        # Log Security Incident
        await log_security_event(
            guild,
            title="ANTI-NUKE TRIGGERED",
            description=f"Mass administrative activity detected by {actor.mention}.",
            fields=[
                ("Executor", f"{actor} ({actor.id})", True),
                ("Trigger Reason", trigger_reason.upper(), True),
                ("Activity Rate", f"`{count}` actions / `{window}s`", True),
                ("Action Configured", action.upper(), True),
                ("Execution Result", result_str, True)
            ],
            color=discord.Color.red()
        )

        await asyncio.sleep(30)
        self.cooldowns.discard(cd_key)

    # ── AUDIT EVENT LISTENERS ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        cfg = get_guild_security_config(guild.id)
        if not cfg.get("enabled") or not cfg["anti_nuke"]["enabled"]:
            return

        actor = await self.identify_executor(guild, discord.AuditLogAction.channel_delete)
        if not actor or actor.id == self.bot.user.id:
            return

        limit = cfg["anti_nuke"]["channel_delete_limit"]
        window = cfg["anti_nuke"]["channel_delete_window"]

        if self.record_and_check(guild.id, actor.id, "channel_delete", limit, window):
            await self.execute_antinuke_sanction(guild, actor, "Channel Deletion Spree", limit, window)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        cfg = get_guild_security_config(guild.id)
        if not cfg.get("enabled") or not cfg["anti_nuke"]["enabled"]:
            return

        actor = await self.identify_executor(guild, discord.AuditLogAction.channel_create)
        if not actor or actor.id == self.bot.user.id:
            return

        limit = cfg["anti_nuke"]["channel_create_limit"]
        window = cfg["anti_nuke"]["channel_create_window"]

        if self.record_and_check(guild.id, actor.id, "channel_create", limit, window):
            await self.execute_antinuke_sanction(guild, actor, "Channel Creation Spree", limit, window)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        cfg = get_guild_security_config(guild.id)
        if not cfg.get("enabled") or not cfg["anti_nuke"]["enabled"]:
            return

        actor = await self.identify_executor(guild, discord.AuditLogAction.role_delete)
        if not actor or actor.id == self.bot.user.id:
            return

        limit = cfg["anti_nuke"]["role_delete_limit"]
        window = cfg["anti_nuke"]["role_delete_window"]

        if self.record_and_check(guild.id, actor.id, "role_delete", limit, window):
            await self.execute_antinuke_sanction(guild, actor, "Role Deletion Spree", limit, window)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        cfg = get_guild_security_config(guild.id)
        if not cfg.get("enabled") or not cfg["anti_nuke"]["enabled"]:
            return

        actor = await self.identify_executor(guild, discord.AuditLogAction.ban)
        if not actor or actor.id == self.bot.user.id:
            return

        limit = cfg["anti_nuke"]["ban_limit"]
        window = cfg["anti_nuke"]["ban_window"]

        if self.record_and_check(guild.id, actor.id, "member_ban", limit, window):
            await self.execute_antinuke_sanction(guild, actor, "Mass Ban Spree", limit, window)

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNukeCog(bot))

