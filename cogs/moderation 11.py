import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
import database as db

# Load bot configuration settings
with open("config.json") as f:
    CONFIG = json.load(f)

SUCCESS = int(CONFIG["bot"]["success_color"])
ERROR   = int(CONFIG["bot"]["error_color"])
WARNING = int(CONFIG["bot"]["warning_color"])
PRIMARY = int(CONFIG["bot"]["color"])

class Moderation(commands.Cog):
    """High-level moderation and staff administration commands for TLC Bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.unmute_loop.start()

    def cog_unload(self):
        self.unmute_loop.cancel()

    async def send_mod_log(self, guild: discord.Guild, embed: discord.Embed):
        """Dispatches moderation embeds to the configured mod log channel."""
        settings = db.get_guild_settings(guild.id)
        if not settings or not settings.get("mod_log_id"):
            return
        ch = guild.get_channel(settings["mod_log_id"])
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.HTTPException:
                pass

    def mod_embed(self, title: str, color: int, **fields) -> discord.Embed:
        """Constructs a standardized moderation log embed."""
        embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
        for name, value in fields.items():
            embed.add_field(name=name.replace("_", " ").title(), value=str(value), inline=True)
        embed.set_footer(text="TLC Bot • Moderation")
        return embed

    # ── BAN COMMAND ───────────────────────────────────────────────────────────

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @commands.has_permissions(administrator=True)
    async def ban(self, ctx: commands.Context,
                  member: discord.Member,
                  reason: str = "No reason provided",
                  delete_days: int = 0):
        if member == ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Ban", description="You cannot ban the server owner.", color=ERROR
            ), ephemeral=True)

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Ban", description="You cannot ban someone with an equal or higher role.", color=ERROR
            ), ephemeral=True)

        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Ban", description="Bot role hierarchy is lower than or equal to the target user.", color=ERROR
            ), ephemeral=True)

        try:
            await member.send(embed=discord.Embed(
                title=f"🔨 You've been banned from {ctx.guild.name}",
                description=f"**Reason:** {reason}", color=ERROR
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass

        delete_days = max(0, min(delete_days, 7))
        await member.ban(reason=reason, delete_message_days=delete_days)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "BAN", member.id, reason)

        embed = self.mod_embed("🔨 Member Banned", ERROR,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            reason=reason
        )
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── UNBAN COMMAND ─────────────────────────────────────────────────────────

    @commands.hybrid_command(name="unban", description="Unban a user by their ID.")
    @commands.has_permissions(administrator=True)
    async def unban(self, ctx: commands.Context, user_id: str, reason: str = "No reason provided"):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await ctx.guild.unban(user, reason=reason)
            db.log_mod_action(ctx.guild.id, ctx.author.id, "UNBAN", user.id, reason)
            
            embed = self.mod_embed("✅ Member Unbanned", SUCCESS,
                user=f"{user} (`{user.id}`)",
                moderator=str(ctx.author),
                reason=reason
            )
            await ctx.send(embed=embed)
            await self.send_mod_log(ctx.guild, embed)
        except (ValueError, discord.NotFound):
            await ctx.send(embed=discord.Embed(
                title="❌ Not Found", description="That user is not banned or the ID provided is invalid.", color=ERROR
            ), ephemeral=True)

    # ── KICK COMMAND ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @commands.has_permissions(administrator=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, reason: str = "No reason provided"):
        if member == ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Kick", description="You cannot kick the server owner.", color=ERROR
            ), ephemeral=True)

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Kick", description="You cannot kick someone with an equal or higher role.", color=ERROR
            ), ephemeral=True)

        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Kick", description="Bot role hierarchy is lower than or equal to the target user.", color=ERROR
            ), ephemeral=True)

        try:
            await member.send(embed=discord.Embed(
                title=f"👢 You've been kicked from {ctx.guild.name}",
                description=f"**Reason:** {reason}", color=WARNING
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass

        await member.kick(reason=reason)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "KICK", member.id, reason)

        embed = self.mod_embed("👢 Member Kicked", WARNING,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            reason=reason
        )
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── MUTE COMMAND ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="mute", description="Mute a member (timeout).")
    @commands.has_permissions(administrator=True)
    async def mute(self, ctx: commands.Context,
                   member: discord.Member,
                   duration: int = 60,
                   reason: str = "No reason provided"):
        if member == ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Mute", description="You cannot mute the server owner.", color=ERROR
            ), ephemeral=True)

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Mute", description="You cannot mute someone with an equal or higher role.", color=ERROR
            ), ephemeral=True)

        # Discord limit on timeout duration is 28 days (40320 minutes)
        duration = max(1, min(duration, 40320))
        until = datetime.now(timezone.utc) + timedelta(minutes=duration)
        
        await member.timeout(until, reason=reason)

        expires_str = until.strftime("%Y-%m-%d %H:%M:%S")
        db.add_mute(ctx.guild.id, member.id, ctx.author.id, reason, expires_str)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "MUTE", member.id, reason, f"{duration}m")

        embed = self.mod_embed("🔇 Member Muted", WARNING,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            duration=f"{duration} minutes",
            reason=reason,
            expires=f"<t:{int(until.timestamp())}:R>"
        )
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── UNMUTE COMMAND ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="unmute", description="Remove a timeout from a member.")
    @commands.has_permissions(administrator=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, reason: str = "Manual unmute"):
        await member.timeout(None, reason=reason)
        db.remove_mute(ctx.guild.id, member.id)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "UNMUTE", member.id, reason)

        embed = self.mod_embed("🔊 Member Unmuted", SUCCESS,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            reason=reason
        )
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── WARN COMMAND ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="warn", description="Warn a standard server member.")
    @commands.has_permissions(administrator=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, reason: str):
        warn_id = db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
        warnings = db.get_warnings(ctx.guild.id, member.id)
        warn_count = len(warnings)
        max_warns = CONFIG.get("moderation", {}).get("max_warn_before_ban", 3)

        try:
            await member.send(embed=discord.Embed(
                title=f"⚠️ Warning in {ctx.guild.name}",
                description=f"**Reason:** {reason}\n**Total Warnings:** {warn_count}/{max_warns}",
                color=WARNING
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass

        db.log_mod_action(ctx.guild.id, ctx.author.id, "WARN", member.id, reason)

        embed = self.mod_embed(f"⚠️ Warning Issued (#{warn_id})", WARNING,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            reason=reason,
            total_warnings=f"{warn_count}/{max_warns}"
        )

        if warn_count >= max_warns:
            embed.add_field(name="⚠️ Auto-Action Warning", value=f"Member has reached {max_warns} warnings — consider escalation.", inline=False)

        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── STAFF WARN COMMAND ───────────────────────────────────────────────────

    @app_commands.command(name="staffwarn", description="Warn a staff member and send them a direct message with the reason.")
    @app_commands.describe(
        member="The staff member you want to issue a warning to.",
        reason="The official reason for issuing this staff warning."
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def staffwarn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        await interaction.response.defer(ephemeral=True)

        if member.bot:
            return await interaction.followup.send("❌ You cannot warn bot accounts.", ephemeral=True)

        target_is_staff = (
            member.guild_permissions.manage_messages or 
            member.guild_permissions.kick_members or 
            member.guild_permissions.ban_members or 
            member.guild_permissions.administrator
        )
        if not target_is_staff:
            return await interaction.followup.send(embed=discord.Embed(
                title="❌ Invalid Target", 
                description="This command is only for warning staff members. Use `/warn` for regular members.", 
                color=ERROR
            ), ephemeral=True)

        if member.top_role >= interaction.user.top_role or member == interaction.guild.owner:
            return await interaction.followup.send(embed=discord.Embed(
                title="❌ Permission Denied", 
                description="You can only warn staff members who have a lower role position than you.", 
                color=ERROR
            ), ephemeral=True)

        warn_id = db.add_warning(interaction.guild_id, member.id, interaction.user.id, f"[STAFF WARN] {reason}")
        warnings = db.get_warnings(interaction.guild_id, member.id)
        warn_count = len(warnings)

        dm_embed = discord.Embed(
            title=f"⚠️ Official Staff Warning — {interaction.guild.name}",
            description="You have received an official staff warning from server administration.",
            color=WARNING
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Issued By", value=interaction.user.mention, inline=True)
        dm_embed.add_field(name="Total Warnings On Record", value=str(warn_count), inline=True)
        dm_embed.set_footer(text="Please ensure adherence to staff protocol.")

        dm_sent = True
        try:
            await member.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        db.log_mod_action(interaction.guild_id, interaction.user.id, "STAFF_WARN", member.id, reason)

        log_embed = self.mod_embed(f"⚠️ Staff Warning Issued (#{warn_id})", WARNING,
            staff_member=f"{member} (`{member.id}`)",
            issued_by=str(interaction.user),
            reason=reason,
            total_warnings=str(warn_count)
        )

        if not dm_sent:
            log_embed.add_field(name="DM Delivery Status", value="⚠️ Direct message failed (DMs closed or blocked).", inline=False)
        else:
            log_embed.add_field(name="DM Delivery Status", value="✅ Direct message successfully delivered.", inline=False)

        await interaction.followup.send(embed=log_embed, ephemeral=True)
        await self.send_mod_log(interaction.guild, log_embed)

    # ── WARNINGS LOOKUP ───────────────────────────────────────────────────────

    @commands.hybrid_command(name="warnings", description="View all warnings for a member.")
    @commands.has_permissions(administrator=True)
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        warns = db.get_warnings(ctx.guild.id, member.id)

        embed = discord.Embed(
            title=f"⚠️ Warnings — {member.display_name}",
            color=WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="TLC Bot • Moderation")

        if not warns:
            embed.description = "✅ This member has no warnings on record."
        else:
            for w in warns[:10]:
                embed.add_field(
                    name=f"#{w['id']} — {w.get('warned_at', 'N/A')[:10]}",
                    value=f"**Reason:** {w['reason']}\n**By:** <@{w['mod_id']}>",
                    inline=False
                )
            if len(warns) > 10:
                embed.set_footer(text=f"Showing 10 of {len(warns)} warnings | TLC Bot")

        await ctx.send(embed=embed)

    # ── CLEAR WARNINGS ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="clearwarnings", description="Clear all warnings for a member.")
    @commands.has_permissions(administrator=True)
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member):
        count = db.clear_warnings(ctx.guild.id, member.id)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "CLEAR_WARNINGS", member.id, f"Cleared {count} warnings")

        embed = self.mod_embed("🗑️ Warnings Cleared", SUCCESS,
            member=f"{member} (`{member.id}`)",
            moderator=str(ctx.author),
            warnings_cleared=str(count)
        )
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── PURGE COMMAND ─────────────────────────────────────────────────────────

    @commands.hybrid_command(name="purge", description="Bulk delete messages in a channel.")
    @commands.has_permissions(administrator=True)
    async def purge(self, ctx: commands.Context, amount: int, member: Optional[discord.Member] = None):
        amount = max(1, min(amount, 100))

        def check(m):
            return member is None or m.author == member

        deleted = await ctx.channel.purge(limit=amount, check=check)
        db.log_mod_action(ctx.guild.id, ctx.author.id, "PURGE", None,
                          f"Deleted {len(deleted)} messages", f"channel:{ctx.channel.id}")

        embed = discord.Embed(
            title="🗑️ Messages Purged",
            description=f"Deleted **{len(deleted)}** messages{f' from {member.mention}' if member else ''}.",
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="TLC Bot • Moderation")
        await ctx.send(embed=embed, ephemeral=True)
        await self.send_mod_log(ctx.guild, embed)

    # ── SLOWMODE COMMAND ──────────────────────────────────────────────────────

    @commands.hybrid_command(name="slowmode", description="Set slowmode in a channel.")
    @commands.has_permissions(administrator=True)
    async def slowmode(self, ctx: commands.Context, seconds: int):
        seconds = max(0, min(seconds, 21600))
        await ctx.channel.edit(slowmode_delay=seconds)

        embed = discord.Embed(
            title="⏱️ Slowmode Updated",
            description=f"Slowmode set to **{seconds} seconds** in {ctx.channel.mention}." if seconds > 0 else f"Slowmode **disabled** in {ctx.channel.mention}.",
            color=SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="TLC Bot • Moderation")
        await ctx.send(embed=embed)

    # ── LOCKDOWN COMMAND ──────────────────────────────────────────────────────

    @commands.hybrid_command(name="lockdown", description="Lock or unlock a channel.")
    @commands.has_permissions(administrator=True)
    async def lockdown(self, ctx: commands.Context, lock: bool, reason: str = "Security measure"):
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = not lock
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)

        action = "🔒 Locked" if lock else "🔓 Unlocked"
        color  = ERROR if lock else SUCCESS
        embed  = discord.Embed(
            title=f"{action}: {ctx.channel.name}",
            description=f"**Reason:** {reason}\n**By:** {ctx.author.mention}",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="TLC Bot • Moderation")
        await ctx.send(embed=embed)
        await self.send_mod_log(ctx.guild, embed)

    # ── MODLOG CONFIGURATION ──────────────────────────────────────────────────

    @commands.hybrid_command(name="modlog", description="Set the moderation log channel.")
    @commands.has_permissions(administrator=True)
    async def modlog(self, ctx: commands.Context, channel: discord.TextChannel):
        db.upsert_guild_settings(ctx.guild.id, mod_log_id=channel.id)
        await ctx.send(embed=discord.Embed(
            title="✅ Mod Log Set",
            description=f"Moderation logs will be sent to {channel.mention}.",
            color=SUCCESS
        ), ephemeral=True)

    # ── UNMUTE LOOP ───────────────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def unmute_loop(self):
        expired = db.get_expired_mutes()
        for mute in expired:
            guild = self.bot.get_guild(mute["guild_id"])
            if not guild:
                continue
            member = guild.get_member(mute["user_id"])
            if member:
                try:
                    await member.timeout(None, reason="Mute expired")
                except Exception:
                    pass
            db.remove_mute(mute["guild_id"], mute["user_id"])

    @unmute_loop.before_loop
    async def before_unmute_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
