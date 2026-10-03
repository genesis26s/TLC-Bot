import discord
from discord.ext import commands, tasks
import json
from datetime import datetime
import psutil
import database as db

with open("config.json") as f:
    CONFIG = json.load(f)

MON_CFG = CONFIG["monitoring"]
SUCCESS = int(CONFIG["bot"]["success_color"])
ERROR   = int(CONFIG["bot"]["error_color"])
PRIMARY = int(CONFIG["bot"]["color"])
WARNING = int(CONFIG["bot"]["warning_color"])

class Monitoring(commands.Cog):
    """Full server monitoring with live dashboard."""

    def __init__(self, bot):
        self.bot = bot
        self._join_history: dict = {}
        self._message_rates: dict = {}
        self.monitor_loop.start()

    def cog_unload(self):
        self.monitor_loop.cancel()

    @commands.hybrid_command(name="serverstats", description="View detailed server statistics.")
    @commands.has_permissions(administrator=True)
    async def serverstats(self, ctx: commands.Context):
        guild = ctx.guild
        now   = datetime.utcnow()

        total   = guild.member_count
        bots    = sum(1 for m in guild.members if m.bot)
        humans  = total - bots
        online  = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
        new_24h = sum(1 for m in guild.members if m.joined_at and (now - m.joined_at.replace(tzinfo=None)).total_seconds() < 86400)

        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)

        boost_level = guild.premium_tier
        boosts      = guild.premium_subscription_count

        embed = discord.Embed(
            title=f"📊 Server Stats — {guild.name}",
            color=PRIMARY,
            timestamp=now
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

        embed.add_field(name="👥 Total Members",   value=f"`{total}`",   inline=True)
        embed.add_field(name="👤 Humans",           value=f"`{humans}`",  inline=True)
        embed.add_field(name="🤖 Bots",             value=f"`{bots}`",   inline=True)
        embed.add_field(name="🟢 Online",           value=f"`{online}`", inline=True)
        embed.add_field(name="📥 Joined 24h",       value=f"`{new_24h}`", inline=True)
        embed.add_field(name="🏆 Boost Level",      value=f"`{boost_level}` ({boosts} boosts)", inline=True)
        embed.add_field(name="💬 Text Channels",    value=f"`{text_ch}`", inline=True)
        embed.add_field(name="🔊 Voice Channels",   value=f"`{voice_ch}`", inline=True)
        embed.add_field(name="📁 Categories",       value=f"`{cats}`",   inline=True)
        embed.add_field(name="😀 Emojis",           value=f"`{len(guild.emojis)}/{guild.emoji_limit}`", inline=True)
        embed.add_field(name="🎭 Roles",            value=f"`{len(guild.roles)}`", inline=True)
        embed.add_field(name="🆔 Server ID",        value=f"`{guild.id}`", inline=True)
        embed.add_field(name="📅 Created",          value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="👑 Owner",            value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="🔐 Verification",     value=str(guild.verification_level).title(), inline=True)

        embed.set_footer(text="TLC Bot • Server Monitor")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="memberinfo", description="View detailed info about a member.")
    @commands.has_permissions(administrator=True)
    async def memberinfo(self, ctx: commands.Context, member: discord.Member):
        warns = db.get_warnings(ctx.guild.id, member.id)
        roles = [r.mention for r in sorted(member.roles[1:], key=lambda r: r.position, reverse=True)]
        now   = datetime.utcnow()

        account_age = (now - member.created_at.replace(tzinfo=None)).days
        server_age  = (now - member.joined_at.replace(tzinfo=None)).days if member.joined_at else 0

        embed = discord.Embed(
            title=f"👤 Member Info — {member}",
            color=member.color if member.color != discord.Color.default() else PRIMARY,
            timestamp=now
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🆔 User ID",      value=f"`{member.id}`", inline=True)
        embed.add_field(name="📅 Account Age",  value=f"`{account_age}d`", inline=True)
        embed.add_field(name="📥 Server Age",   value=f"`{server_age}d`", inline=True)
        embed.add_field(name="🎭 Highest Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="⚠️ Warnings",     value=f"`{len(warns)}`", inline=True)
        embed.add_field(name="🤖 Bot",          value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="📋 Status",       value=str(member.status).title(), inline=True)
        embed.add_field(name="🎮 Activity",     value=str(member.activity.name) if member.activity else "None", inline=True)
        embed.add_field(name="🔇 Timed Out",    value="Yes" if member.timed_out_until else "No", inline=True)
        if roles:
            embed.add_field(name=f"🎭 Roles ({len(roles)})", value=" ".join(roles[:10]) + ("..." if len(roles) > 10 else ""), inline=False)
        embed.set_footer(text="TLC Bot • Member Info")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="roleinfo", description="View info about a role.")
    @commands.has_permissions(administrator=True)
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        member_count = len(role.members)

        embed = discord.Embed(
            title=f"🎭 Role Info — {role.name}",
            color=role.color if role.color != discord.Color.default() else PRIMARY,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="🆔 Role ID",      value=f"`{role.id}`",          inline=True)
        embed.add_field(name="👥 Members",       value=f"`{member_count}`",     inline=True)
        embed.add_field(name="🎨 Color",         value=str(role.color),         inline=True)
        embed.add_field(name="📌 Position",      value=f"`{role.position}`",    inline=True)
        embed.add_field(name="📢 Mentionable",   value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="📌 Hoisted",       value="Yes" if role.hoist else "No",       inline=True)
        embed.add_field(name="🤖 Managed",       value="Yes" if role.managed else "No",     inline=True)
        embed.add_field(name="🛡️ Admin",         value="Yes" if role.permissions.administrator else "No", inline=True)
        embed.add_field(name="📅 Created",       value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True)
        embed.set_footer(text="TLC Bot • Role Info")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="botinfo", description="View TLC Bot's system information.")
    @commands.has_permissions(administrator=True)
    async def botinfo(self, ctx: commands.Context):
        bot = self.bot

        try:
            cpu  = psutil.cpu_percent(interval=0.5)
            ram  = psutil.virtual_memory()
            ram_used = round(ram.used / 1024**2, 1)
            ram_total = round(ram.total / 1024**2, 1)
        except Exception:
            cpu = ram_used = ram_total = "N/A"

        embed = discord.Embed(
            title="🤖 TLC Bot — System Info",
            color=PRIMARY,
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.add_field(name="🏷️ Bot Name",    value=str(bot.user),                  inline=True)
        embed.add_field(name="🆔 Bot ID",      value=f"`{bot.user.id}`",             inline=True)
        embed.add_field(name="📡 Latency",     value=f"`{round(bot.latency*1000)}ms`", inline=True)
        embed.add_field(name="🌐 Servers",     value=f"`{len(bot.guilds)}`",          inline=True)
        embed.add_field(name="👥 Users",       value=f"`{sum(g.member_count for g in bot.guilds)}`", inline=True)
        embed.add_field(name="📋 Commands",    value=f"`{len(bot.tree.get_commands())}`", inline=True)
        embed.add_field(name="💻 CPU",         value=f"`{cpu}%`",                    inline=True)
        embed.add_field(name="🧠 RAM",         value=f"`{ram_used}/{ram_total} MB`", inline=True)
        embed.add_field(name="📚 discord.py",  value=f"`{discord.__version__}`",     inline=True)
        embed.set_footer(text="TLC Bot • System Monitor")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="setalertchannel", description="Set the channel for security alerts.")
    @commands.has_permissions(administrator=True)
    async def setalertchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        db.upsert_guild_settings(ctx.guild.id, alert_channel=channel.id)
        await ctx.send(embed=discord.Embed(
            title="✅ Alert Channel Set",
            description=f"Security alerts will be sent to {channel.mention}.",
            color=SUCCESS
        ), ephemeral=True)

    @tasks.loop(seconds=MON_CFG["update_interval_seconds"])
    async def monitor_loop(self):
        if not MON_CFG["enabled"]:
            return

        for guild in self.bot.guilds:
            settings = db.get_guild_settings(guild.id)
            if not settings or not settings.get("alert_channel"):
                continue

            events = db.get_security_events(guild.id, limit=20)
            recent_critical = [e for e in events
                                if e["severity"] in ("high", "critical")
                                and (datetime.utcnow() - datetime.fromisoformat(e["logged_at"])).total_seconds() < 300]

            if len(recent_critical) >= MON_CFG["suspicious_activity_threshold"]:
                alert_ch = guild.get_channel(settings["alert_channel"])
                if alert_ch:
                    embed = discord.Embed(
                        title="🚨 Suspicious Activity Detected",
                        description=f"**{len(recent_critical)}** high-severity security events in the last 5 minutes.",
                        color=ERROR,
                        timestamp=datetime.utcnow()
                    )
                    embed.set_footer(text="TLC Bot • Live Monitor")
                    try:
                        await alert_ch.send(embed=embed)
                    except Exception:
                        pass

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Monitoring(bot))

