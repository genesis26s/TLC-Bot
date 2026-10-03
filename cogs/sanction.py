import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from datetime import datetime, timezone
import logging

logger = logging.getLogger("TLCBot.Sanctions")

DB_FILE = "bot_data.db"

# ── DATABASE INITIALIZATION & MIGRATION ──────────────────────────────────────

def init_sanctions_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sanctions (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            discord_user_id INTEGER NOT NULL,
            roblox_username TEXT NOT NULL,
            reason TEXT NOT NULL,
            bail_amount INTEGER DEFAULT 0,
            issued_by_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            gameweeks INTEGER,
            status TEXT DEFAULT 'active' -- 'active', 'lifted', 'expired'
        )
    """)

    # 2. Migration: Safely add 'gameweeks' column if it doesn't exist yet
    cursor.execute("PRAGMA table_info(sanctions)")
    columns = [column[1] for column in cursor.fetchall()]
    if "gameweeks" not in columns:
        cursor.execute("ALTER TABLE sanctions ADD COLUMN gameweeks INTEGER")
        logger.info("Migrated SQLite database: Added 'gameweeks' column to sanctions table.")

    conn.commit()
    conn.close()

init_sanctions_db()

# ── COG IMPLEMENTATION ────────────────────────────────────────────────────────

class SanctionCog(commands.Cog):
    """League Sanction & Case Management System with Gameweek (GW) Tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── STAFF CHECK ───────────────────────────────────────────────────────────

    def is_staff():
        async def predicate(interaction: discord.Interaction) -> bool:
            perms = interaction.user.guild_permissions
            if perms.administrator or perms.manage_roles or perms.manage_guild or perms.manage_messages:
                return True
            
            user_roles = [r.name.lower() for r in interaction.user.roles]
            return any(kw in name for name in user_roles for kw in ["staff", "mod", "admin", "evaluator"])
        return app_commands.check(predicate)

    # ── COMMAND 1: /sanction (Issue Sanction with Gameweeks) ───────────────────

    @app_commands.command(name="sanction", description="Issue a league sanction against a player with optional Gameweeks duration.")
    @app_commands.describe(
        discord_user="The Discord member receiving the sanction",
        roblox_username="The player's Roblox username",
        reason="Reason for the sanction",
        bail_amount="Robux bail amount required to lift early (0 if none)",
        gameweeks="Number of Gameweeks (GWs) the ban lasts (e.g. 5). Leave blank for permanent."
    )
    @is_staff()
    async def issue_sanction(
        self,
        interaction: discord.Interaction,
        discord_user: discord.Member,
        roblox_username: str,
        reason: str,
        bail_amount: int = 0,
        gameweeks: int = None
    ):
        await interaction.response.defer()

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        # Insert record into SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sanctions (guild_id, discord_user_id, roblox_username, reason, bail_amount, issued_by_id, created_at, gameweeks, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (interaction.guild_id, discord_user.id, roblox_username, reason, bail_amount, interaction.user.id, now_iso, gameweeks))
        case_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Format Gameweek display string
        if gameweeks is not None:
            gw_str = f"**{gameweeks} GW{'s' if gameweeks != 1 else ''}**"
        else:
            gw_str = "`Indefinite / Permanent`"

        # Construct Card Embed
        embed = discord.Embed(
            title=f"⚖️ LEAGUE SANCTION ISSUED — Case #TLC-{case_id}",
            description=f"A formal sanction has been registered against {discord_user.mention}.",
            color=discord.Color.red(),
            timestamp=now_dt
        )
        embed.add_field(name="👤 Discord Member", value=f"{discord_user.mention}\n`@{discord_user.name}`", inline=True)
        embed.add_field(name="🎮 Roblox User", value=f"`{roblox_username}`", inline=True)
        embed.add_field(name="💰 Bail Amount", value=f"`{bail_amount} R$`" if bail_amount > 0 else "`No Bail`", inline=True)
        embed.add_field(name="📄 Reason", value=reason, inline=False)
        embed.add_field(name="⏳ Sanction Duration", value=gw_str, inline=False)

        embed.set_footer(text=f"Issued by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        await interaction.followup.send(content=f"🚨 **Sanction Case #TLC-{case_id} Logged:**", embed=embed)

    # ── COMMAND 2: /lift_sanction (Manual Early Lift) ─────────────────────────

    @app_commands.command(name="lift_sanction", description="Manually lift an active sanction case (Staff Only).")
    @app_commands.describe(case_id="The numeric Case ID (e.g. 1071)")
    @is_staff()
    async def lift_sanction(self, interaction: discord.Interaction, case_id: int):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT discord_user_id, roblox_username, status FROM sanctions WHERE case_id = ? AND guild_id = ?", (case_id, interaction.guild_id))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return await interaction.response.send_message(f"❌ Case `#TLC-{case_id}` was not found in this server.", ephemeral=True)

        user_id, roblox_user, status = row

        if status != "active":
            conn.close()
            return await interaction.response.send_message(f"⚠️ Case `#TLC-{case_id}` is already `{status}`.", ephemeral=True)

        cursor.execute("UPDATE sanctions SET status = 'lifted' WHERE case_id = ?", (case_id,))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"✅ Sanction Case `#TLC-{case_id}` for **{roblox_user}** (<@{user_id}>) has been manually **LIFTED**.")

    # ── COMMAND 3: /sanctions (Public Case Lookup) ─────────────────────────────

    @app_commands.command(name="sanctions", description="View active or past sanction records for a member.")
    @app_commands.describe(member="The member whose sanction history to view (defaults to you)")
    async def list_sanctions(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT case_id, roblox_username, reason, bail_amount, status, gameweeks
            FROM sanctions
            WHERE guild_id = ? AND discord_user_id = ?
            ORDER BY case_id DESC
        """, (interaction.guild_id, target.id))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return await interaction.response.send_message(f"✅ {target.mention} has a clean record (0 sanctions on file).", ephemeral=True)

        embed = discord.Embed(
            title=f"📜 Sanction History — {target.display_name}",
            description=f"Recorded cases for {target.mention}:",
            color=discord.Color.dark_theme()
        )

        for case_id, roblox_user, reason, bail, status, gameweeks in rows[:5]:
            status_icon = "🔴" if status == "active" else ("🟢" if status == "lifted" else "⚪")
            
            gw_display = f"{gameweeks} GW{'s' if gameweeks != 1 else ''}" if gameweeks is not None else "Indefinite"

            embed.add_field(
                name=f"{status_icon} Case #TLC-{case_id} [{status.upper()}]",
                value=f"**Roblox:** `{roblox_user}` | **Bail:** `{bail} R$`\n**Reason:** {reason}\n**Duration:** {gw_display}",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    # ── SAFE ERROR HANDLER ───────────────────────────────────────────────────

    @issue_sanction.error
    async def sanction_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = "❌ **Access Denied:** Only staff can issue sanctions." if isinstance(error, app_commands.CheckFailure) else f"❌ Error: {str(error)}"
        
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SanctionCog(bot))
