import json
import os
import discord
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("TLCBot.Security")

SECURITY_CONFIG_FILE = "security_config.json"

DEFAULT_GUILD_SECURITY = {
    "enabled": True,
    "security_log_channel_id": None,
    "lockdown": False,
    "whitelist_users": [],
    "whitelist_roles": [],
    "exempt_channels": [],
    "anti_nuke": {
        "enabled": True,
        "action": "timeout",  # log_only, warn, timeout, kick, ban
        "channel_delete_limit": 3,
        "channel_delete_window": 10,
        "channel_create_limit": 5,
        "channel_create_window": 10,
        "role_delete_limit": 3,
        "role_delete_window": 10,
        "role_create_limit": 5,
        "role_create_window": 10,
        "ban_limit": 4,
        "ban_window": 15,
        "kick_limit": 4,
        "kick_window": 15,
        "webhook_limit": 4,
        "webhook_window": 15
    },
    "anti_spam": {
        "enabled": True,
        "action": "timeout",  # delete, warn, timeout, kick, ban
        "message_limit": 5,
        "message_window": 4,
        "duplicate_limit": 3,
        "duplicate_window": 6,
        "mention_limit": 6,
        "link_limit": 4,
        "emoji_limit": 10,
        "character_limit": 1000,
        "timeout_duration_minutes": 15
    },
    "anti_raid": {
        "enabled": True,
        "action": "lockdown",  # log, alert, timeout, kick, ban, lockdown
        "join_limit": 10,
        "join_window": 15,
        "min_account_age_days": 3,
        "critical_risk_score": 75,
        "auto_lockdown_on_high_risk": True
    }
}

# ── PERSISTENCE ENGINE ────────────────────────────────────────────────────────

def load_master_security_config() -> dict:
    if not os.path.exists(SECURITY_CONFIG_FILE):
        with open(SECURITY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=4, ensure_ascii=False)
        return {}
    try:
        with open(SECURITY_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading security_config.json: {e}")
        return {}

def save_master_security_config(config: dict):
    try:
        with open(SECURITY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving security_config.json: {e}")

def get_guild_security_config(guild_id: int) -> dict:
    master = load_master_security_config()
    str_gid = str(guild_id)
    if str_gid not in master:
        master[str_gid] = json.loads(json.dumps(DEFAULT_GUILD_SECURITY))
        save_master_security_config(master)
        return master[str_gid]
    return master[str_gid]

def save_guild_security_config(guild_id: int, guild_cfg: dict):
    master = load_master_security_config()
    master[str(guild_id)] = guild_cfg
    save_master_security_config(master)

# ── HIERARCHY & PERMISSION CHECKS ─────────────────────────────────────────────

def is_whitelisted(member: discord.Member, cfg: dict) -> bool:
    """Checks if a user or any of their roles are explicitly whitelisted."""
    if member.guild.owner_id == member.id:
        return True
    
    if member.id in cfg.get("whitelist_users", []):
        return True

    member_role_ids = {r.id for r in member.roles}
    whitelisted_role_ids = set(cfg.get("whitelist_roles", []))
    if member_role_ids.intersection(whitelisted_role_ids):
        return True

    return False

def can_moderate_user(guild: discord.Guild, actor: discord.Member, target: discord.Member) -> bool:
    """Ensures bot and actor respect Discord role hierarchy before taking action."""
    if target.id == guild.owner_id:
        return False
    
    me = guild.me
    if me.top_role <= target.top_role:
        return False

    return True

# ── UNIFIED SECURITY EVENT LOGGER ─────────────────────────────────────────────

async def log_security_event(guild: discord.Guild, title: str, description: str, fields: list = None, color: discord.Color = discord.Color.red()):
    cfg = get_guild_security_config(guild.id)
    
    log_channel_id = cfg.get("security_log_channel_id")
    log_channel = None
    
    if log_channel_id:
        log_channel = guild.get_channel(log_channel_id)
        
    if not log_channel:
        # Fallback to existing moderation logs or system channel
        log_channel = guild.system_channel or next((c for c in guild.text_channels if "log" in c.name.lower() or "security" in c.name.lower()), None)

    if not log_channel:
        return

    embed = discord.Embed(
        title=f"🛡️ SECURITY ALERT — {title}",
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=f"TLC-Bot Security System • Guild ID: {guild.id}")

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to dispatch security log in guild {guild.id}: {e}")

