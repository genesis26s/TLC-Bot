import discord
from discord.ext import commands
import json
from datetime import datetime, timezone
from typing import Optional
import database as db

with open("config.json") as f:
    CONFIG = json.load(f)

SUCCESS = int(CONFIG["bot"]["success_color"])
ERROR   = int(CONFIG["bot"]["error_color"])
PRIMARY = int(CONFIG["bot"]["color"])

def format_welcome_vars(text: str, member: discord.Member) -> str:
    """Replaces placeholders in both <tag> and {tag} styles."""
    if not text:
        return ""
    
    replacements = {
        # Modern <variable> format
        "<user_mention>": member.mention,
        "<username>": member.name,
        "<user_id>": str(member.id),
        "<server_name>": member.guild.name,
        "<member_count>": str(member.guild.member_count),
        
        # Legacy {variable} format fallback
        "{user}": member.mention,
        "{username}": member.name,
        "{id}": str(member.id),
        "{server}": member.guild.name,
        "{count}": str(member.guild.member_count)
    }

    formatted = text
    for key, val in replacements.items():
        formatted = formatted.replace(key, val)
    return formatted

class WelcomeView(discord.ui.View):
    """Optional action row attached to welcome messages for quick server navigation."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        
        rules_channel_id = CONFIG["welcome"].get("rules_channel_id")
        roles_channel_id = CONFIG["welcome"].get("roles_channel_id")

        if rules_channel_id:
            self.add_item(discord.ui.Button(
                label="Read Rules", 
                style=discord.ButtonStyle.link, 
                url=f"https://discord.com/channels/{guild.id}/{rules_channel_id}",
                emoji="📜"
            ))
            
        if roles_channel_id:
            self.add_item(discord.ui.Button(
                label="Get Roles", 
                style=discord.ButtonStyle.link, 
                url=f"https://discord.com/channels/{guild.id}/{roles_channel_id}",
                emoji="🎭"
            ))

class Welcome(commands.Cog):
    """Modern Welcoming & Goodbye System with rich embeds, variable replacement, and interactive links."""

    def __init__(self, bot):
        self.bot = bot

    def _build_welcome_embed(self, member: discord.Member, config: dict) -> discord.Embed:
        guild = member.guild

        raw_title = config.get("title") or CONFIG["welcome"].get("title", "👋 Welcome to <server_name>!")
        raw_desc  = config.get("description") or CONFIG["welcome"].get("description", "Welcome <user_mention>! We're thrilled to have you here.")
        raw_footer = config.get("footer") or CONFIG["welcome"].get("footer", "TLC Bot • Member #<member_count>")

        title_text = format_welcome_vars(raw_title, member)
        desc_text  = format_welcome_vars(raw_desc, member)
        footer_text = format_welcome_vars(raw_footer, member)
        embed_color = config.get("color") or int(CONFIG["welcome"].get("color", PRIMARY))

        embed = discord.Embed(
            title=title_text,
            description=f"{desc_text}\n\n"
                        f"🏆 **Member Count:** `#<member_count>`\n"
                        f"📅 **Account Created:** <t:{int(member.created_at.timestamp())}:R>".replace("<member_count>", str(guild.member_count)),
            color=embed_color,
            timestamp=datetime.now(timezone.utc)
        )

        # Set clean user avatar as thumbnail (or custom thumbnail if provided)
        if config.get("thumbnail_url"):
            embed.set_thumbnail(url=config["thumbnail_url"])
        else:
            embed.set_thumbnail(url=member.display_avatar.url)

        # Custom Banner / Image
        if config.get("image_url"):
            embed.set_image(url=config["image_url"])

        # Styled Footer with Issuer/Bot info
        embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild.icon else None)

        return embed

    # ── EVENT: MEMBER JOIN ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        db_config = db.get_welcome_config(guild.id) or {}
        if not db_config.get("enabled", CONFIG["welcome"].get("enabled", True)):
            return

        settings = db.get_guild_settings(guild.id) or {}
        ch_id    = settings.get("welcome_channel")
        channel  = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name=CONFIG["welcome"]["channel_name"])
        if not channel:
            return

        embed = self._build_welcome_embed(member, db_config)
        
        # Attach welcome buttons view if configured
        view = WelcomeView(guild) if CONFIG["welcome"].get("enable_buttons") else None

        await channel.send(content=f"🎉 Welcome to **{guild.name}**, {member.mention}!", embed=embed, view=view)

        # Optional DM Welcome Message with Variables
        if CONFIG["welcome"].get("dm_on_join"):
            try:
                raw_dm = CONFIG["welcome"].get("dm_message", "Welcome to <server_name>, <user_mention>!")
                dm_text = format_welcome_vars(raw_dm, member)
                
                dm_embed = discord.Embed(
                    title=f"Welcome to {guild.name}! 👋",
                    description=dm_text,
                    color=PRIMARY,
                    timestamp=datetime.now(timezone.utc)
                )
                if guild.icon:
                    dm_embed.set_thumbnail(url=guild.icon.url)
                dm_embed.set_footer(text=f"TLC Bot • Official Welcome")
                await member.send(embed=dm_embed)
            except Exception:
                pass

    # ── EVENT: MEMBER REMOVE (GOODBYE) ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not CONFIG["goodbye"].get("enabled", True):
            return
        
        guild    = member.guild
        settings = db.get_guild_settings(guild.id) or {}
        ch_id    = settings.get("goodbye_channel")
        channel  = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name=CONFIG["goodbye"]["channel_name"])
        if not channel:
            return

        raw_title = CONFIG["goodbye"].get("title", "👋 Member Departed")
        raw_desc  = CONFIG["goodbye"].get("description", "**<username>** has left the server.\nWe now have **<member_count>** members.")

        embed = discord.Embed(
            title=format_welcome_vars(raw_title, member),
            description=format_welcome_vars(raw_desc, member),
            color=int(CONFIG["goodbye"].get("color", ERROR)),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="TLC Bot • Goodbye Log", icon_url=guild.icon.url if guild.icon else None)
        
        await channel.send(embed=embed)

    # ── COMMANDS ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="setwelcome", description="Configure the welcome message for new members.")
    @commands.has_permissions(administrator=True)
    async def setwelcome(self, ctx: commands.Context,
                          channel: discord.TextChannel,
                          title: Optional[str] = None,
                          description: Optional[str] = None,
                          image_url: Optional[str] = None,
                          thumbnail_url: Optional[str] = None,
                          footer: Optional[str] = None,
                          color: Optional[str] = None):
        try:
            color_int = int(color.strip("#"), 16) if color else PRIMARY
        except Exception:
            color_int = PRIMARY

        update = {"enabled": 1}
        if title:         update["title"]         = title
        if description:   update["description"]   = description
        if image_url:     update["image_url"]      = image_url
        if thumbnail_url: update["thumbnail_url"]  = thumbnail_url
        if footer:        update["footer"]         = footer
        if color:         update["color"]          = color_int

        db.upsert_welcome_config(ctx.guild.id, **update)
        db.upsert_guild_settings(ctx.guild.id, welcome_channel=channel.id)

        preview_config = db.get_welcome_config(ctx.guild.id) or {}
        preview_embed  = self._build_welcome_embed(ctx.author, preview_config)
        preview_embed.set_author(name="📋 Preview — Welcome Message", icon_url=ctx.author.display_avatar.url)

        await ctx.send(
            content=f"✅ **Welcome channel updated to {channel.mention}.** Here is a preview of your message layout:",
            embed=preview_embed,
            ephemeral=True
        )

    @commands.hybrid_command(name="testwelcome", description="Send a test welcome message in the designated channel.")
    @commands.has_permissions(administrator=True)
    async def testwelcome(self, ctx: commands.Context):
        guild    = ctx.guild
        settings = db.get_guild_settings(guild.id) or {}
        ch_id    = settings.get("welcome_channel")
        channel  = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name=CONFIG["welcome"]["channel_name"])

        if not channel:
            return await ctx.send(embed=discord.Embed(
                title="❌ Welcome Channel Not Set",
                description="Please configure a welcome channel first using `/setwelcome`.",
                color=ERROR
            ), ephemeral=True)

        db_config = db.get_welcome_config(guild.id) or {}
        embed     = self._build_welcome_embed(ctx.author, db_config)
        embed.set_author(name="🧪 Test Welcome Message", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        
        view = WelcomeView(guild) if CONFIG["welcome"].get("enable_buttons") else None
        
        await channel.send(content=f"🎉 Welcome to **{guild.name}**, {ctx.author.mention}!", embed=embed, view=view)
        await ctx.send(f"✅ Test welcome message successfully sent to {channel.mention}!", ephemeral=True)

    @commands.hybrid_command(name="setwelcomeimage", description="Set the banner image URL for the welcome embed.")
    @commands.has_permissions(administrator=True)
    async def setwelcomeimage(self, ctx: commands.Context, url: str):
        db.upsert_welcome_config(ctx.guild.id, image_url=url)
        embed = discord.Embed(
            title="✅ Welcome Banner Updated",
            description="The welcome banner image has been updated successfully.",
            color=SUCCESS
        )
        embed.set_image(url=url)
        embed.set_footer(text="TLC Bot • Welcome Configuration")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="setgoodbyechannel", description="Set the goodbye message channel.")
    @commands.has_permissions(administrator=True)
    async def setgoodbyechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        db.upsert_guild_settings(ctx.guild.id, goodbye_channel=channel.id)
        await ctx.send(embed=discord.Embed(
            title="✅ Goodbye Channel Configured",
            description=f"Goodbye messages will now be logged in {channel.mention}.",
            color=SUCCESS
        ), ephemeral=True)

    @commands.hybrid_command(name="disablewelcome", description="Disable the welcome message system.")
    @commands.has_permissions(administrator=True)
    async def disablewelcome(self, ctx: commands.Context):
        db.upsert_welcome_config(ctx.guild.id, enabled=0)
        await ctx.send(embed=discord.Embed(
            title="🔕 Welcome System Disabled",
            description="Welcome messages have been turned off for this server.",
            color=ERROR
        ), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Welcome(bot))
