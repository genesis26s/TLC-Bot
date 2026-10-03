import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import os
from datetime import datetime
import logging

logger = logging.getLogger("TLCBot.PlayerRatings")

DB_FILE = "player_ratings.db"

# ── DATABASE INITIALIZATION ──────────────────────────────────────────────────

def init_ratings_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_ratings (
            guild_id INTEGER NOT NULL,
            discord_user_id INTEGER NOT NULL,
            roblox_username TEXT NOT NULL,
            roblox_user_id INTEGER,
            rating TEXT NOT NULL,
            position TEXT NOT NULL,
            player_class TEXT NOT NULL,
            evaluator_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, discord_user_id)
        )
    """)
    conn.commit()
    conn.close()

init_ratings_db()

# ── COG IMPLEMENTATION ────────────────────────────────────────────────────────

class PlayerRatings(commands.Cog):
    """Official Soccer/Football Player Evaluation & Profile System for TLC League."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── ROBLOX API FETCH HELPER ───────────────────────────────────────────────

    async def fetch_roblox_profile_and_avatar(self, roblox_username: str):
        """Asynchronously fetches Roblox User ID, Profile URL, and Headshot Avatar Image."""
        async with aiohttp.ClientSession() as session:
            try:
                # Step 1: Resolve Username -> Roblox User ID
                payload = {"usernames": [roblox_username], "excludeBannedUsers": False}
                async with session.post("https://users.roblox.com/v1/usernames/users", json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("data") and len(data["data"]) > 0:
                            user_data = data["data"][0]
                            user_id = user_data["id"]
                            profile_url = f"https://www.roblox.com/users/{user_id}/profile"

                            # Step 2: Fetch Avatar Headshot Image
                            thumb_endpoint = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
                            async with session.get(thumb_endpoint, timeout=5) as thumb_resp:
                                if thumb_resp.status == 200:
                                    thumb_data = await thumb_resp.json()
                                    if thumb_data.get("data") and len(thumb_data["data"]) > 0:
                                        img_url = thumb_data["data"][0].get("imageUrl")
                                        return user_id, profile_url, img_url

                            return user_id, profile_url, "https://tr.rbxcdn.com/30day-avatar-headshot"

            except Exception as e:
                logger.error(f"Failed to resolve Roblox info for {roblox_username}: {e}")

        search_fallback_url = f"https://www.roblox.com/search/users?keyword={roblox_username}"
        fallback_avatar = "https://tr.rbxcdn.com/30day-avatar-headshot"
        return None, search_fallback_url, fallback_avatar

    # ── STAFF PERMISSION CHECK ────────────────────────────────────────────────

    def is_staff():
        """Requires Administrator, Manage Roles, Manage Guild, or a Staff/Evaluator role."""
        async def predicate(interaction: discord.Interaction) -> bool:
            perms = interaction.user.guild_permissions
            if perms.administrator or perms.manage_roles or perms.manage_guild or perms.manage_messages:
                return True
            
            user_role_names = [r.name.lower() for r in interaction.user.roles]
            staff_keywords = ["staff", "moderator", "admin", "evaluator", "scout", "manager"]
            if any(kw in name for name in user_role_names for kw in staff_keywords):
                return True

            return False
        return app_commands.check(predicate)

    # ── COMMAND 1: /rate (Staff Only) ─────────────────────────────────────────

    @app_commands.command(name="rate", description="Evaluate a player and save/post their official rating card (Staff Only).")
    @app_commands.describe(
        discord_user="The Discord member being rated",
        roblox_username="The player's exact Roblox username",
        rating="Numerical overall rating score (e.g. 88, 95)",
        position="Soccer / Football playing position",
        player_class="Player Class (C-Class, B-Class, A-Class, A+-Class, S-Class, S+-Class, X-Class)",
        custom_avatar_url="Optional image URL override for the player avatar"
    )
    @app_commands.choices(
        position=[
            app_commands.Choice(name="Goalkeeper (GK)", value="Goalkeeper (GK)"),
            app_commands.Choice(name="Center Back (CB)", value="Center Back (CB)"),
            app_commands.Choice(name="Fullback (LB/RB)", value="Fullback (LB/RB)"),
            app_commands.Choice(name="Defensive Midfielder (CDM)", value="Defensive Midfielder (CDM)"),
            app_commands.Choice(name="Central Midfielder (CM)", value="Central Midfielder (CM)"),
            app_commands.Choice(name="Attacking Midfielder (CAM)", value="Attacking Midfielder (CAM)"),
            app_commands.Choice(name="Winger (LW/RW)", value="Winger (LW/RW)"),
            app_commands.Choice(name="Striker (ST/CF)", value="Striker (ST/CF)"),
            app_commands.Choice(name="Utility (UTIL)", value="Utility (UTIL)")
        ],
        player_class=[
            app_commands.Choice(name="X-Class", value="X-Class"),
            app_commands.Choice(name="S+-Class", value="S+-Class"),
            app_commands.Choice(name="S-Class", value="S-Class"),
            app_commands.Choice(name="A+-Class", value="A+-Class"),
            app_commands.Choice(name="A-Class", value="A-Class"),
            app_commands.Choice(name="B-Class", value="B-Class"),
            app_commands.Choice(name="C-Class", value="C-Class")
        ]
    )
    @is_staff()
    async def rate(
        self,
        interaction: discord.Interaction,
        discord_user: discord.Member,
        roblox_username: str,
        rating: str,
        position: str,
        player_class: str,
        custom_avatar_url: str = None
    ):
        await interaction.response.defer()

        # Fetch Roblox Details
        user_id, profile_url, avatar_url = await self.fetch_roblox_profile_and_avatar(roblox_username)

        if custom_avatar_url and custom_avatar_url.startswith("http"):
            avatar_url = custom_avatar_url

        # Save / Update in SQLite Database
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO player_ratings (guild_id, discord_user_id, roblox_username, roblox_user_id, rating, position, player_class, evaluator_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, discord_user_id) DO UPDATE SET
                roblox_username = excluded.roblox_username,
                roblox_user_id = excluded.roblox_user_id,
                rating = excluded.rating,
                position = excluded.position,
                player_class = excluded.player_class,
                evaluator_id = excluded.evaluator_id,
                updated_at = excluded.updated_at
        """, (interaction.guild_id, discord_user.id, roblox_username, user_id, rating, position, player_class, interaction.user.id, now_str))
        conn.commit()
        conn.close()

        # Build Card Embed Color Mapping
        tier_colors = {
            "X-Class": discord.Color.from_rgb(255, 0, 128),
            "S+-Class": discord.Color.gold(),
            "S-Class": discord.Color.yellow(),
            "A+-Class": discord.Color.purple(),
            "A-Class": discord.Color.blue(),
            "B-Class": discord.Color.green(),
            "C-Class": discord.Color.light_grey()
        }
        embed_color = tier_colors.get(player_class, discord.Color.gold())

        embed = discord.Embed(
            title=f"⚽ PLAYER EVALUATION — {roblox_username.upper()}",
            url=profile_url,
            description=f"Official league evaluation card issued for {discord_user.mention}.",
            color=embed_color
        )

        embed.add_field(name="👤 Discord Member", value=f"{discord_user.mention}\n`@{discord_user.name}`", inline=True)
        embed.add_field(name="🎮 Roblox Profile", value=f"[{roblox_username}]({profile_url})\n`ID: {user_id if user_id else 'Unverified'}`", inline=True)
        embed.add_field(name="⚡ Rating", value=f"**` {rating} `**", inline=True)
        embed.add_field(name="⚽ Position", value=f"`{position}`", inline=True)
        embed.add_field(name="🏆 Class", value=f"**` {player_class} `**", inline=True)

        embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"TLC Football Evaluation • Evaluated by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(content=f"📢 **Player Evaluation Saved & Issued for** {discord_user.mention}:", embed=embed)

    # ── COMMAND 2: /rate_profile (Public) ─────────────────────────────────────

    @app_commands.command(name="rate_profile", description="View the official player rating evaluation profile of a member.")
    @app_commands.describe(member="The Discord member whose profile rating you want to view (defaults to you)")
    async def rate_profile(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()

        target_member = member or interaction.user

        # Fetch rating profile from SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT roblox_username, roblox_user_id, rating, position, player_class, evaluator_id, updated_at
            FROM player_ratings
            WHERE guild_id = ? AND discord_user_id = ?
        """, (interaction.guild_id, target_member.id))
        row = cursor.fetchone()
        conn.close()

        if not row:
            msg = f"❌ **No Rating Profile Found:** {target_member.mention} has not been officially evaluated in this server yet."
            return await interaction.followup.send(content=msg, ephemeral=True)

        roblox_username, roblox_user_id, rating, position, player_class, evaluator_id, updated_at = row

        # Fetch fresh Roblox profile URL & Avatar thumbnail
        user_id, profile_url, avatar_url = await self.fetch_roblox_profile_and_avatar(roblox_username)

        evaluator = interaction.guild.get_member(evaluator_id)
        evaluator_name = evaluator.display_name if evaluator else "League Evaluator"

        tier_colors = {
            "X-Class": discord.Color.from_rgb(255, 0, 128),
            "S+-Class": discord.Color.gold(),
            "S-Class": discord.Color.yellow(),
            "A+-Class": discord.Color.purple(),
            "A-Class": discord.Color.blue(),
            "B-Class": discord.Color.green(),
            "C-Class": discord.Color.light_grey()
        }
        embed_color = tier_colors.get(player_class, discord.Color.gold())

        embed = discord.Embed(
            title=f"📋 PLAYER PROFILE CARD — {roblox_username.upper()}",
            url=profile_url,
            description=f"Registered rating profile for {target_member.mention}.",
            color=embed_color
        )

        embed.add_field(name="👤 Discord Account", value=f"{target_member.mention}\n`@{target_member.name}`", inline=True)
        embed.add_field(name="🎮 Roblox Account", value=f"[{roblox_username}]({profile_url})\n`ID: {roblox_user_id or 'N/A'}`", inline=True)
        embed.add_field(name="⚡ Overall Rating", value=f"**` {rating} `**", inline=True)
        embed.add_field(name="⚽ Primary Position", value=f"`{position}`", inline=True)
        embed.add_field(name="🏆 Class", value=f"**` {player_class} `**", inline=True)
        embed.add_field(name="✍️ Evaluated By", value=f"`{evaluator_name}`", inline=True)

        embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"Last Updated: {updated_at}")

        await interaction.followup.send(embed=embed)

    @rate.error
    async def rate_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("❌ **Access Denied:** Only staff and league evaluators can use `/rate`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error generating evaluation: {str(error)}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerRatings(bot))