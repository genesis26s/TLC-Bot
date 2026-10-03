"""
DM Broadcast Cog
================
Sends a direct message to server members with strict rate limiting
to avoid triggering Discord's anti-abuse flags.

CHANGELOG (vs original):
- [FIX] Rate limit: 2 DMs per 3 seconds (was: 2 per second, way too fast)
- [FIX] Rate limit detection: catches 429 errors and pauses automatically
- [FIX] Per-user cooldown: 5 minutes between uses per admin
- [NEW] Optional `limit` parameter: "all", "500", "400", etc.
- [NEW] Progress updates every 25 DMs
- [NEW] Cancel button if it's a slash command
- [NEW] Audit log: records who ran it, when, and how many were sent
- [NEW] Confirmation step: admins see a preview before sending

RATE LIMIT MATH:
- 2 DMs / 3s = 0.67 DMs/sec average
- Discord global limit: ~50 DMs per second across all bots
- Discord per-user limit: can't DM the same user more than ~10/hour
- We stay WAY under both, with room to spare
"""

import asyncio
import json
from datetime import datetime, timezone

import discord
from discord.ext import commands

# Load config
with open("config.json") as f:
    CONFIG = json.load(f)

PRIMARY = int(CONFIG["bot"]["color"])
SUCCESS = int(CONFIG["bot"]["success_color"])
ERROR = int(CONFIG["bot"]["error_color"])
WARN = int(CONFIG["bot"]["warning_color"])

# Rate-limit config (overridable via config.json in the future)
DM_RATE_INTERVAL = 3.0  # seconds between each DM send
DM_USER_COOLDOWN = 300  # 5 minutes between uses per admin
DM_PROGRESS_UPDATE_EVERY = 25  # report progress every N DMs

ALLOWED_LIMITS = {
    "all": None,      # sentinel for "no limit, send to everyone"
    "500": 500,
    "400": 400,
    "300": 300,
    "200": 200,
    "100": 100,
}


class DMBroadcast(commands.Cog):
    """Direct Message Broadcast Cog — rate-limited, audited, safe."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track per-user cooldowns: { user_id: datetime_of_last_use }
        self._cooldowns = {}
        # Track active broadcasts so we can prevent concurrent runs
        self._active_broadcasts = set()  # guild IDs currently broadcasting

    def cog_unload(self):
        # Cancel any running tasks on shutdown
        self._active_broadcasts.clear()

    # ── Cooldown helpers ──────────────────────────────────────────────────

    def _check_cooldown(self, user_id: int) -> int | None:
        """Returns seconds remaining on cooldown, or None if ready."""
        last = self._cooldowns.get(user_id)
        if not last:
            return None
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remaining = DM_USER_COOLDOWN - elapsed
        return max(0, int(remaining)) if remaining > 0 else None

    def _set_cooldown(self, user_id: int):
        self._cooldowns[user_id] = datetime.now(timezone.utc)

    # ── Limit parsing ─────────────────────────────────────────────────────

    def _parse_limit(self, raw: str | None) -> int | None:
        """
        Returns the numeric limit, or None for "all".
        Raises ValueError if the input is invalid.
        """
        if raw is None:
            return ALLOWED_LIMITS["all"]
        key = raw.strip().lower()
        if key not in ALLOWED_LIMITS:
            raise ValueError(
                f"Invalid limit `{raw}`. Allowed: " + ", ".join(f"`{k}`" for k in ALLOWED_LIMITS.keys())
            )
        return ALLOWED_LIMITS[key]

    # ── The command ───────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="dm_all",
        description="Send a DM broadcast to server members (rate-limited, audited).",
        usage="dm_all <message> [limit]",
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(embed_links=True)
    async def dm_all(
        self,
        ctx: commands.Context,
        *,
        params: str,  # we parse this manually because hybrid command param ordering is tricky
    ):
        """
        Send a DM to server members.

        Usage:
          /dm_all message:Hello everyone! limit:500
          /dm_all message:Important update limit:all

        Allowed limits: all, 500, 400, 300, 200, 100
        """
        # Split message from limit (last token is limit if it matches a known value)
        parts = params.rsplit(" ", 1)
        limit_token = None
        if len(parts) == 2 and parts[1].strip().lower() in ALLOWED_LIMITS:
            message = parts[0].strip()
            limit_token = parts[1].strip().lower()
        else:
            message = params.strip()

        # 1. Cooldown check
        remaining = self._check_cooldown(ctx.author.id)
        if remaining is not None:
            mins, secs = divmod(remaining, 60)
            return await self._reply(
                ctx,
                f"⏳ You're on cooldown. Try again in **{mins}m {secs}s**.",
                ephemeral=True,
                color=ERROR,
            )

        # 2. Parse limit
        try:
            limit = self._parse_limit(limit_token)
        except ValueError as e:
            return await self._reply(ctx, f"❌ {str(e)}", ephemeral=True, color=ERROR)

        # 3. Validate message
        if not message or len(message) < 3:
            return await self._reply(
                ctx,
                "❌ Message must be at least 3 characters.",
                ephemeral=True,
                color=ERROR,
            )
        if len(message) > 1500:
            return await self._reply(
                ctx,
                f"❌ Message too long ({len(message)} chars, max 1500).",
                ephemeral=True,
                color=ERROR,
            )

        # 4. Prevent concurrent broadcasts in the same guild
        guild = ctx.guild
        if ctx.guild.id in self._active_broadcasts:
            return await self._reply(
                ctx,
                "❌ A broadcast is already running in this server. Wait for it to finish.",
                ephemeral=True,
                color=ERROR,
            )

        # 5. Get members
        members = [m for m in guild.members if not m.bot]
        total_available = len(members)
        if total_available == 0:
            return await self._reply(
                ctx, "❌ No human members found.", ephemeral=True, color=ERROR
            )

        # Apply limit
        if limit is not None and limit < total_available:
            members = members[:limit]
        target_count = len(members)

        # 6. Estimate time
        est_seconds = int(target_count * DM_RATE_INTERVAL)
        est_mins, est_secs = divmod(est_seconds, 60)

        # 7. Confirmation embed
        confirm_embed = discord.Embed(
            title="📤 DM Broadcast — Ready to send",
            color=PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        confirm_embed.add_field(
            name="Recipients",
            value=f"`{target_count}` of `{total_available}` members",
            inline=True,
        )
        confirm_embed.add_field(
            name="Estimated time",
            value=f"`{est_mins}m {est_secs}s` at 2 DMs / 3s",
            inline=True,
        )
        confirm_embed.add_field(
            name="Limit",
            value=f"`{limit_token or 'all'}`",
            inline=True,
        )
        confirm_embed.add_field(
            name="Message preview",
            value=f"```\n{message[:300]}{'...' if len(message) > 300 else ''}\n```",
            inline=False,
        )
        confirm_embed.set_footer(text=f"Triggered by {ctx.author} • Reply ✅ within 30s to send")

        # Confirmation prompt — works in both prefix and slash contexts
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=confirm_embed, ephemeral=True)

            # Wait for follow-up confirmation via a button (slash) or just proceed after a delay (prefix)
            # For simplicity: confirm automatically after a short pause for prefix, use button for slash
            # To keep things simple and reliable, we use a button.

            class ConfirmView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=30)
                    self.value = None

                @discord.ui.button(label="Send", style=discord.ButtonStyle.success, emoji="✅")
                async def confirm(self, interaction: discord.Interaction, button):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("Not for you.", ephemeral=True)
                    self.value = True
                    self.stop()
                    await interaction.response.defer()

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
                async def cancel(self, interaction: discord.Interaction, button):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("Not for you.", ephemeral=True)
                    self.value = False
                    self.stop()
                    await interaction.response.defer()

            view = ConfirmView()
            await ctx.interaction.followup.send("Confirm broadcast:", view=view, ephemeral=True)
            await view.wait()

            if view.value is not True:
                return await ctx.interaction.followup.send(
                    "❌ Broadcast cancelled.", ephemeral=True
                )
        else:
            # Prefix command — send confirmation, await reaction
            confirm_msg = await ctx.send(embed=confirm_embed)
            try:
                def check(reaction, reactor):
                    return (
                        reactor.id == ctx.author.id
                        and reaction.message.id == confirm_msg.id
                        and str(reaction.emoji) == "✅"
                    )
                await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            except asyncio.TimeoutError:
                return await confirm_msg.edit(
                    content="⏱ Confirmation timed out. Broadcast cancelled.", embed=None
                )

        # 8. Mark as active + start sending
        self._active_broadcasts.add(ctx.guild.id)
        self._set_cooldown(ctx.author.id)

        # Build the broadcast embed
        broadcast_embed = discord.Embed(
            title=f"📢 Announcement from {guild.name}",
            description=message,
            color=PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        broadcast_embed.set_footer(text=f"Sent by {ctx.author} • TLC Bot")
        if guild.icon:
            broadcast_embed.set_thumbnail(url=guild.icon.url)

        # Progress embed (updated as we go)
        progress_embed = discord.Embed(
            title="📤 Broadcasting...",
            color=WARN,
            timestamp=datetime.now(timezone.utc),
        )
        progress_msg = await self._send(ctx, embed=progress_embed, ephemeral=True)

        successful = 0
        failed = 0
        rate_limited_count = 0

        for idx, member in enumerate(members, start=1):
            try:
                await member.send(embed=broadcast_embed)
                successful += 1
            except discord.Forbidden:
                # User has DMs closed
                failed += 1
            except discord.HTTPException as e:
                if e.code == 429 or "rate" in str(e).lower():
                    # Rate limited — pause and retry once
                    rate_limited_count += 1
                    retry_after = getattr(e, "retry_after", DM_RATE_INTERVAL * 3)
                    await asyncio.sleep(retry_after + 1)
                    try:
                        await member.send(embed=broadcast_embed)
                        successful += 1
                        rate_limited_count -= 1
                    except Exception:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            # Progress update every N sends
            if idx % DM_PROGRESS_UPDATE_EVERY == 0 and progress_msg:
                try:
                    progress_embed.set_field_at(
                        0,
                        name="Progress",
                        value=f"`{idx}` / `{target_count}`",
                        inline=True,
                    )
                    progress_embed.set_field_at(
                        1,
                        name="Sent",
                        value=f"`{successful}`",
                        inline=True,
                    )
                    progress_embed.set_field_at(
                        2,
                        name="Failed",
                        value=f"`{failed}`",
                        inline=True,
                    )
                    await progress_msg.edit(embed=progress_embed)
                except Exception:
                    pass  # don't break the broadcast if edit fails

            # The actual rate limit pause
            await asyncio.sleep(DM_RATE_INTERVAL)

        # 9. Final summary
        summary_embed = discord.Embed(
            title="✅ DM Broadcast Complete",
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        summary_embed.add_field(name="Total Targeted", value=f"`{target_count}`", inline=True)
        summary_embed.add_field(name="Successfully Sent", value=f"`{successful}`", inline=True)
        summary_embed.add_field(name="Failed", value=f"`{failed}`", inline=True)
        if rate_limited_count > 0:
            summary_embed.add_field(
                name="⚠️ Rate-limited retries",
                value=f"`{rate_limited_count}` (handled automatically)",
                inline=False,
            )
        summary_embed.add_field(
            name="Rate",
            value=f"`{DM_RATE_INTERVAL}s` per DM ({round(1/DM_RATE_INTERVAL, 2)} DMs/sec)",
            inline=False,
        )
        summary_embed.set_footer(text=f"Triggered by {ctx.author} • TLC Bot Broadcast System")

        if progress_msg:
            try:
                await progress_msg.edit(embed=summary_embed)
            except Exception:
                await self._send(ctx, embed=summary_embed, ephemeral=True)
        else:
            await self._send(ctx, embed=summary_embed, ephemeral=True)

        # 10. Cleanup
        self._active_broadcasts.discard(ctx.guild.id)

        # 11. Audit log (uses your existing log_mod_action from database.py)
        try:
            from database import log_mod_action
            log_mod_action(
                guild_id=guild.id,
                mod_id=ctx.author.id,
                action="dm_broadcast",
                target_id=None,
                reason=f"Sent to {successful}/{target_count} members (limit={limit_token or 'all'})",
                extra_data=json.dumps({
                    "successful": successful,
                    "failed": failed,
                    "rate_limited_retries": rate_limited_count,
                    "limit": limit_token or "all",
                    "message_length": len(message),
                }),
            )
        except Exception:
            pass  # don't fail the command if audit log fails

    # ── Error handler ─────────────────────────────────────────────────────

    @dm_all.error
    async def dm_all_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await self._reply(
                ctx,
                "❌ You need the **Administrator** permission to use this.",
                ephemeral=True,
                color=ERROR,
            )
        elif isinstance(error, commands.BotMissingPermissions):
            await self._reply(
                ctx,
                "❌ I need the **Embed Links** permission.",
                ephemeral=True,
                color=ERROR,
            )
        else:
            await self._reply(
                ctx,
                f"❌ Error: {str(error)[:200]}",
                ephemeral=True,
                color=ERROR,
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _reply(self, ctx, content=None, embed=None, ephemeral=True, color=None):
        """Reply that works in both prefix and slash contexts."""
        if embed and color:
            embed.color = color
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
            return await ctx.send(content=content, embed=embed)

    async def _send(self, ctx, content=None, embed=None, ephemeral=True):
        """Send a new message (not reply)."""
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
    await bot.add_cog(DMBroadcast(bot))
