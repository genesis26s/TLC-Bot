import discord
from discord.ext import commands
import asyncio
import logging
import json
import os
import sys
import platform
import sqlite3
import psutil
from datetime import datetime, timezone
from database import init_database
from dotenv import load_dotenv

load_dotenv()

# ── Load Config ───────────────────────────────────────────────────────────────
with open("config.json", "r") as f:
    CONFIG = json.load(f)

# ── Logging Setup ─────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"logs/tlcbot_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TLCBot")

# ── Bot Setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

bot.config = CONFIG
bot.owner_ids_list = [int(uid) for uid in CONFIG.get("owners", [])]

# ── Cogs to Load ──────────────────────────────────────────────────────────────
COGS = [
    "cogs.verification",
    "cogs.moderation",
    "cogs.security",
    "cogs.logging_cog",
    "cogs.tickets",
    "cogs.admin",
    "cogs.monitoring",
    "cogs.welcome",
    "cogs.sanction",
    "cogs.dm_broadcast",
    "cogs.api_server",
    "cogs.security_config",
    "cogs.anti_nuke",
    "cogs.anti_spam",
    "cogs.anti_raid",
    "cogs.player_rating",
    "cogs.friendly",
    "cogs.leaderboard",
    "cogs.tlc_transfer_cog",
    "cogs.staff_warn_cog",
    "cogs.roblox_usernames",
]

# ── Diagnostic Telemetry Suite ────────────────────────────────────────────────
async def log_bot_diagnostics():
    logger.info("=" * 65)
    logger.info("             🚀 TLCBOT DIAGNOSTIC & DEBUG SUITE 🚀             ")
    logger.info("=" * 65)
    logger.info(f" [1] Bot Identity         : {bot.user} (ID: {bot.user.id})")
    logger.info(f" [2] Bot Owners           : {bot.owner_ids_list}")
    logger.info(f" [3] App ID               : {bot.application_id}")
    logger.info(f" [4] Python Version       : {platform.python_version()}")
    logger.info(f" [5] discord.py Version   : {discord.__version__}")
    logger.info(f" [6] Operating System     : {platform.system()} {platform.release()} ({platform.machine()})")
    logger.info(f" [7] CPU Core Count       : {psutil.cpu_count(logical=True)} Cores ({psutil.cpu_percent()}% Usage)")
    ram = psutil.virtual_memory()
    logger.info(f" [8] RAM Utilization      : {ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent}%)")

    total_members = sum(g.member_count or 0 for g in bot.guilds)
    logger.info(f" [9] Connected Guilds     : {len(bot.guilds)}")
    logger.info(f"[10] Total Cached Members : {total_members}")
    logger.info(f"[11] Gateway Latency      : {round(bot.latency * 1000, 2)} ms")
    logger.info(f"[12] Shard Count          : {bot.shard_count or 1}")

    tree_commands = bot.tree.get_commands()
    logger.info(f"[13] Loaded Cogs          : {len(bot.cogs)} / {len(COGS)}")
    logger.info(f"[14] Slash Commands Sync  : {len(tree_commands)} Global Commands")
    logger.info(f"[15] Text Prefix Commands : {len(bot.commands)}")

    logger.info(f"[16] Members Intent       : {'ENABLED' if intents.members else 'DISABLED'}")
    logger.info(f"[17] Presences Intent     : {'ENABLED' if intents.presences else 'DISABLED'}")
    logger.info(f"[18] Message Content      : {'ENABLED' if intents.message_content else 'DISABLED'}")

    db_status = "UNKNOWN"
    try:
        conn = sqlite3.connect("bot_data.db")
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cur.fetchall()]
        conn.close()
        db_status = f"ONLINE ({len(tables)} Tables Active)"
    except Exception as ex:
        db_status = f"ERROR: {ex}"

    logger.info(f"[19] SQLite Database      : {db_status}")
    logger.info(f"[20] API Server Listening : 0.0.0.0:30088")
    logger.info(f"[21] System Time (UTC)    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 65)

@bot.event
async def on_ready():
    await bot.tree.sync()
    await log_bot_diagnostics()

    status_map = {
        "playing":   discord.ActivityType.playing,
        "watching":  discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing,
    }
    activity_type = status_map.get(CONFIG["bot"]["status_type"], discord.ActivityType.watching)
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=activity_type, name=CONFIG["bot"]["status"])
    )

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"Joined guild: {guild.name} (ID: {guild.id} | Members: {guild.member_count})")

@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command Error in [{ctx.command}]: {error}")

def is_bot_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id not in bot.owner_ids_list:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Access Denied",
                    description="This command is reserved for the **Bot Owner** only.",
                    color=int(CONFIG["bot"]["error_color"])
                ),
                ephemeral=True
            )
            return False
        return True
    return discord.app_commands.check(predicate)

def is_server_admin():
    async def predicate(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Access Denied",
                    description="You need the **Administrator** permission to use this command.",
                    color=int(CONFIG["bot"]["error_color"])
                ),
                ephemeral=True
            )
            return False
        return True
    return discord.app_commands.check(predicate)

bot.is_bot_owner = is_bot_owner
bot.is_server_admin = is_server_admin

async def main():
    init_database()
    logger.info("Database initialized.")

    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                logger.info(f"  ✅ Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"  ❌ Failed to load cog {cog}: {e}")

        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.critical("DISCORD_TOKEN not found in environment.")
            sys.exit(1)

        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
