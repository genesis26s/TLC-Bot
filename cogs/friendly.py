"""
Friendly Cog
============
A "friendly message" system for the TLC league Discord.

Workflow:
  1. Admin: /setfriendlyroles @RoleA @RoleB
  2. Admin: /setfriendlychannel #friendly-feed
  3. Anyone with RoleA OR RoleB: /lf_friendly <message>
     → Posts embed in configured channel
     → Pings RoleA and RoleB
     → DMs every member with RoleA OR RoleB (rate-limited)
     → Reports delivery stats

CHANGELOG:
- [NEW] /setfriendlychannel - configure destination channel
- [NEW] /setfriendlyroles   - configure 2 roles
- [NEW] /lf_friendly        - send the friendly message
- [SAFETY] 0.5 DMs/sec rate limit (2 per 4 seconds) — no flagging
- [SAFETY] Per-executor cooldown (3 min) to prevent abuse
- [SAFETY] DMs users with role1 OR role2 only
- [SAFETY] Error message for users without those roles
- [SAFETY] Configurable per guild (SQLite-persisted)
- [SAFETY] Audit log entry on every send
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

import database

# ── Config ───────────────────────────────────────────────────────────────────
DM_RATE_INTERVAL = 4.0        # seconds between each DM
USER_COOLDOWN = 180            # 3 minutes between /lf_friendly uses per user
MAX_MESSAGE_LENGTH = 1500
PROGRESS_UPDATE_EVERY = 10

PRIMARY = 0x8b5cf6   # violet
SUCCESS = 0x10b981
ERROR = 0xef4444
WARN = 0xf59e0b


class FriendlyCog(commands.Cog):
    """Friendly message broadcast system for TLC."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[int, datetime] = {}  # user_id → last_use
        self._active: set[int] = set()             # guild_ids currently sending

    def cog_unload(self):
        self._active.clear()

    # ── Cooldown helpers ──────────────────────────────────────────────────

    def _cooldown_remaining(self, user_id: int) -> Optional[int]:
        last = self._cooldowns.get(user_id)
        if not last:
            return None
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remaining = USER_COOLDOWN - elapsed
        return max(0, int(remaining)) if remaining > 0 else None

    def _set_cooldown(self, user_id: int):
        self._cooldowns[user_id] = datetime.now(timezone.utc)

    # ── Setup commands ────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="setfriendlychannel",
        description="Set the channel where /lf_friendly embeds are posted.",
        usage="setfriendlychannel #channel",
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(embed_links=True)
    async def set_friendly_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        if not ctx.guild:
            return await self._reply(ctx, "❌ Server-only command.", ephemeral=True)

        database.upsert_friendly_settings(
            ctx.guild.id,
            channel_id=channel.id,
        )

        embed = discord.Embed(
            title="✅ Friendly channel set",
            description=f"Future `/lf_friendly` embeds will be posted in {channel.mention}.",
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Configured by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self._reply(ctx, embed=embed, ephemeral=True)

    @set_friendly_channel.error
    async def set_friendly_channel_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await self._reply(ctx, "❌ Admins only.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await self._reply(ctx, "❌ Please mention a valid text channel.", ephemeral=True)

    @commands.hybrid_command(
        name="setfriendlyroles",
        description="Set the 2 roles that can use /lf_friendly and will be DM'd by it.",
        usage="setfriendlyroles @Role1 @Role2",
    )
    @commands.has_permissions(administrator=True)
    async def set_friendly_roles(
        self,
        ctx: commands.Context,
        role1: discord.Role,
        role2: discord.Role,
    ):
        if not ctx.guild:
            return await self._reply(ctx, "❌ Server-only command.", ephemeral=True)

        if role1.id == role2.id:
            return await self._reply(
                ctx, "❌ Role 1 and Role 2 must be different.", ephemeral=True
            )

        # Disallow @everyone for safety
        if role1.is_default() or role2.is_default():
            return await self._reply(
                ctx,
                "❌ You can't use `@everyone` here — that would DM every member.",
                ephemeral=True,
            )

        database.upsert_friendly_settings(
            ctx.guild.id,
            role_1_id=role1.id,
            role_2_id=role2.id,
        )

        embed = discord.Embed(
            title="✅ Friendly roles set",
            description=(
                f"Members with {role1.mention} **or** {role2.mention} can now run "
                f"`/lf_friendly` and will be DM'd when it's used."
            ),
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Configured by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self._reply(ctx, embed=embed, ephemeral=True)

    @set_friendly_roles.error
    async def set_friendly_roles_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await self._reply(ctx, "❌ Admins only.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await self._reply(
                ctx,
                "❌ Please mention two valid roles. Usage: `/setfriendlyroles @Role1 @Role2`",
                ephemeral=True,
            )

    # ── The main command ──────────────────────────────────────────────────

    @commands.hybrid_command(
        name="lf_friendly",
        description="Send a friendly message + DM all members with the configured roles.",
        usage="lf_friendly <your message>",
    )
    @commands.bot_has_permissions(embed_links=True)
    async def lf_friendly(self, ctx: commands.Context, *, message: str):
        if not ctx.guild:
            return await self._reply(ctx, "❌ Server-only command.", ephemeral=True)

        # 1. Get config
        settings = database.get_friendly_settings(ctx.guild.id)
        if not settings or not settings.get("channel_id") or not settings.get("role_1_id") or not settings.get("role_2_id"):
            return await self._reply(
                ctx,
                "❌ Friendly system isn't configured yet. Ask an admin to run "
                "`/setfriendlychannel` and `/setfriendlyroles` first.",
                ephemeral=True,
            )

        channel_id = settings["channel_id"]
        role_1_id = settings["role_1_id"]
        role_2_id = settings["role_2_id"]

        channel = ctx.guild.get_channel(channel_id)
        role1 = ctx.guild.get_role(role_1_id)
        role2 = ctx.guild.get_role(role_2_id)

        if not channel or not role1 or not role2:
            return await self._reply(
                ctx,
                "❌ Configured channel or roles no longer exist. Ask an admin to reconfigure.",
                ephemeral=True,
            )

        # 2. Permission check — user must have role1 OR role2
        user_role_ids = {r.id for r in ctx.author.roles}
        if role_1_id not in user_role_ids and role_2_id not in user_role_ids:
            return await self._reply(
                ctx,
                f"You don't have any of the roles, "
                f"{role1.mention} or {role2.mention}",
                ephemeral=True,
            )

        # 3. Cooldown
        remaining = self._cooldown_remaining(ctx.author.id)
        if remaining is not None:
            mins, secs = divmod(remaining, 60)
            return await self._reply(
                ctx,
                f"⏳ You're on cooldown. Try again in **{mins}m {secs}s**.",
                ephemeral=True,
            )

        # 4. Validate message
        if not message or len(message.strip()) < 3:
            return await self._reply(
                ctx, "❌ Message must be at least 3 characters.", ephemeral=True
            )
        if len(message) > MAX_MESSAGE_LENGTH:
            return await self._reply(
                ctx,
                f"❌ Message too long ({len(message)} chars, max {MAX_MESSAGE_LENGTH}).",
                ephemeral=True,
            )

        # 5. Prevent concurrent broadcasts
        if ctx.guild.id in self._active:
            return await self._reply(
                ctx,
                "❌ A friendly message is already being sent. Wait for it to finish.",
                ephemeral=True,
            )

        # 6. Build the recipient list (anyone with role1 OR role2, exclude bots + executor)
        recipients = set()
        for member in ctx.guild.members:
            if member.bot or member.id == ctx.author.id:
                continue
            member_role_ids = {r.id for r in member.roles}
            if role_1_id in member_role_ids or role_2_id in member_role_ids:
                recipients.add(member)

        recipients = list(recipients)
        total = len(recipients)

        if total == 0:
            return await self._reply(
                ctx,
                f"❌ No members found with {role1.mention} or {role2.mention} (excluding bots and yourself).",
                ephemeral=True,
            )

        # 7. Estimate time
        est_seconds = int(total * DM_RATE_INTERVAL)
        est_mins, est_secs = divmod(est_seconds, 60)

        # 8. Build the embed that goes in the channel
        channel_embed = discord.Embed(
            title="💚 Friendly Message",
            description=message,
            color=PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        channel_embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        channel_embed.set_footer(
            text=f"TLC Friendly System • DMing {total} members",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )

        # 9. Post the embed in the configured channel + ping both roles
        try:
            await channel.send(
                content=f"{role1.mention} {role2.mention}",
                embed=channel_embed,
            )
        except discord.Forbidden:
            return await self._reply(
                ctx,
                f"❌ I don't have permission to post in {channel.mention}.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            return await self._reply(
                ctx,
                f"❌ Failed to post in {channel.mention}: {str(e)[:100]}",
                ephemeral=True,
            )

        # 10. Lock + cooldown
        self._active.add(ctx.guild.id)
        self._set_cooldown(ctx.author.id)

        # 11. Build the DM embed
        dm_embed = discord.Embed(
            title=f"💚 Friendly message from {ctx.guild.name}",
            description=message,
            color=PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        dm_embed.set_footer(text="TLC Friendly System")

        # 12. Progress message
        progress_embed = discord.Embed(
            title="📤 Sending DMs...",
            color=WARN,
            timestamp=datetime.now(timezone.utc),
        )
        progress_embed.add_field(name="Recipients", value=f"`{total}`", inline=True)
        progress_embed.add_field(name="Rate", value=f"`{DM_RATE_INTERVAL}s` per DM", inline=True)
        progress_embed.add_field(name="ETA", value=f"`{est_mins}m {est_secs}s`", inline=True)
        progress_msg = await self._send(ctx, embed=progress_embed, ephemeral=True)

        # 13. Send the DMs
        successful = 0
        failed = 0
        rate_limited_count = 0

        for idx, member in enumerate(recipients, start=1):
            try:
                await member.send(embed=dm_embed)
                successful += 1
            except discord.Forbidden:
                failed += 1
            except discord.HTTPException as e:
                if e.code == 429 or "rate" in str(e).lower():
                    rate_limited_count += 1
                    retry_after = getattr(e, "retry_after", DM_RATE_INTERVAL * 2)
                    await asyncio.sleep(retry_after + 1)
                    try:
                        await member.send(embed=dm_embed)
                        successful += 1
                        rate_limited_count -= 1
                    except Exception:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            # Progress update
            if idx % PROGRESS_UPDATE_EVERY == 0 and progress_msg:
                try:
                    progress_embed.set_field_at(
                        0, name="Recipients", value=f"`{idx}` / `{total}`", inline=True
                    )
                    progress_embed.set_field_at(
                        1, name="Sent", value=f"`{successful}`", inline=True
                    )
                    progress_embed.set_field_at(
                        2, name="Failed", value=f"`{failed}`", inline=True
                    )
                    progress_embed.set_field_at(
                        3, name="ETA", value=f"`{est_mins}m {est_secs}s`", inline=True
                    )
                    await progress_msg.edit(embed=progress_embed)
                except Exception:
                    pass

            await asyncio.sleep(DM_RATE_INTERVAL)

        # 14. Final summary
        summary_embed = discord.Embed(
            title="✅ Friendly message sent",
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        summary_embed.add_field(name="Posted in", value=channel.mention, inline=True)
        summary_embed.add_field(name="Pinged", value=f"{role1.mention}, {role2.mention}", inline=True)
        summary_embed.add_field(name="Targeted", value=f"`{total}` members", inline=False)
        summary_embed.add_field(name="DMs Sent", value=f"`{successful}`", inline=True)
        summary_embed.add_field(name="DMs Failed", value=f"`{failed}`", inline=True)
        if rate_limited_count > 0:
            summary_embed.add_field(
                name="⚠️ Rate-limited retries",
                value=f"`{rate_limited_count}` (handled)",
                inline=True,
            )
        summary_embed.set_footer(text=f"Triggered by {ctx.author}")

        if progress_msg:
            try:
                await progress_msg.edit(embed=summary_embed)
            except Exception:
                await self._send(ctx, embed=summary_embed, ephemeral=True)
        else:
            await self._send(ctx, embed=summary_embed, ephemeral=True)

        # 15. Cleanup
        self._active.discard(ctx.guild.id)

        # 16. Audit log
        try:
            database.log_mod_action(
                guild_id=ctx.guild.id,
                mod_id=ctx.author.id,
                action="friendly_broadcast",
                target_id=None,
                reason=f"Friendly message sent to {successful}/{total} members",
                extra_data=json.dumps({
                    "successful": successful,
                    "failed": failed,
                    "rate_limited_retries": rate_limited_count,
                    "channel_id": channel_id,
                    "role_1_id": role_1_id,
                    "role_2_id": role_2_id,
                    "message_length": len(message),
                }),
            )
        except Exception:
            pass

    @lf_friendly.error
    async def lf_friendly_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await self._reply(ctx, "❌ I need Embed Links permission.", ephemeral=True)
        else:
            await self._reply(
                ctx, f"❌ Error: {str(error)[:200]}", ephemeral=True
            )

    # ── Helpers ───────────────────────────────────────────────────────────

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

    async def _send(self, ctx, content=None, embed=None, ephemeral=True):
        try:
            if ctx.interaction:
                if ctx.interaction.response.is_done():
                    return await ctx.interaction.followup.send(
                        content=content, embed=embed, ephemeral=ephemeral
                    )
                return await ctx.interaction.response.send_message(
                    content=content, embed=embed, ephemeral=ephemeral
                )
            return await ctx.send(content=content, embed=embed)
        except Exception:
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(FriendlyCog(bot))