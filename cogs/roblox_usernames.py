import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
from typing import Optional

class PublicBloxlinkNicknameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_roblox_username(
        self, 
        session: aiohttp.ClientSession, 
        discord_id: int
    ) -> Optional[str]:
        """Queries Bloxlink's Public API and resolves the current Roblox Username."""
        bloxlink_url = f"https://api.blox.link/v4/public/users/{discord_id}"

        roblox_id = None
        try:
            async with session.get(bloxlink_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    roblox_id = data.get("robloxID")
                elif resp.status == 404:
                    return None
                else:
                    return None
        except Exception:
            return None

        if not roblox_id:
            return None

        roblox_user_url = f"https://users.roblox.com/v1/users/{roblox_id}"
        try:
            async with session.get(roblox_user_url) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    return user_data.get("name")
        except Exception:
            return None

        return None

    # ── SLASH COMMAND ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="reset_nicknames_to_roblox",
        description="Mass resets server members' nicknames to match their verified Roblox usernames."
    )
    # Restrict to users with Administrator permission
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    # Cooldown: 1 use every 300 seconds (5 minutes) per user to protect API rate limits
    @app_commands.checks.cooldown(1, 300.0, key=lambda i: (i.guild_id, i.user.id))
    async def reset_nicknames(self, interaction: discord.Interaction):
        # Defer immediately to avoid Discord's 3-second slash command timeout
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ This command must be used in a Discord server.")

        updated_count = 0
        skipped_unverified = 0
        skipped_hierarchy = 0
        failed_errors = 0

        async with aiohttp.ClientSession() as session:
            async for member in guild.fetch_members(limit=None):
                # Skip bots and Server Owner
                if member.bot or member.id == guild.owner_id:
                    continue

                # Skip members who have equal or higher roles than the bot
                if guild.me.top_role <= member.top_role:
                    skipped_hierarchy += 1
                    continue

                # Fetch Bloxlink-linked Username
                roblox_username = await self.get_roblox_username(session, member.id)

                if roblox_username:
                    # Skip if the user's nickname is already identical
                    if member.nick == roblox_username:
                        continue

                    try:
                        await member.edit(nick=roblox_username, reason="Mass Roblox Nickname Reset")
                        updated_count += 1
                        
                        # 0.4s delay per member prevents Discord nickname edit rate limits
                        await asyncio.sleep(0.4)
                    except discord.Forbidden:
                        failed_errors += 1
                    except discord.HTTPException:
                        failed_errors += 1
                else:
                    skipped_unverified += 1

        embed = discord.Embed(
            title="🔄 Mass Roblox Nickname Sync Complete",
            color=discord.Color.green()
        )
        embed.add_field(name="Successfully Renamed", value=f"`{updated_count}` members", inline=False)
        embed.add_field(name="Skipped (Not Verified on Bloxlink)", value=f"`{skipped_unverified}` members", inline=False)
        embed.add_field(name="Skipped (Role Higher/Equal to Bot)", value=f"`{skipped_hierarchy}` members", inline=False)
        embed.add_field(name="Failed (Permissions/HTTP Error)", value=f"`{failed_errors}` members", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── ERROR HANDLER (COOLDOWN & PERMISSION CHECKS) ──────────────────────────

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Catches permission and cooldown errors specifically for slash commands in this cog."""
        if isinstance(error, app_commands.CommandOnCooldown):
            minutes, seconds = divmod(int(error.retry_after), 60)
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            
            await interaction.response.send_message(
                f"⏳ **Command Cooldown:** This command can only be run once every 5 minutes.\n"
                f"Please wait **{time_str}** before trying again.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ **Access Denied:** You need **Administrator** permissions to execute this command.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ An error occurred: `{str(error)}`",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(PublicBloxlinkNicknameCog(bot))
