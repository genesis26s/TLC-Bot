from dataclasses import dataclass, field
from typing import Optional, List

# ── NETWORK & HOSTING CONFIGURATION ──────────────────────────────────────────
# Your live host IP and designated port
WEB_SERVER_URL = "https://genesis26s-tlc-bot-website-xi.vercel.app"

# Database Storage File
DB_FILE = "bot_data.db"

# ── ANTI-ALT & SECURITY THRESHOLDS ───────────────────────────────────────────
# Minimum Discord account age required to pass Layer 1 (in days)
MIN_DISCORD_AGE_DAYS = 14

# Minimum Roblox account age required to pass Layer 4 (in days)
MIN_ROBLOX_AGE_DAYS = 30

# Maximum aggregate risk score permitted before triggering a quarantine/block
MAX_ALLOWED_RISK_SCORE = 75

# Threat Intelligence API Keys (Optional: Leave empty for free-tier / default limits)
PROXYCHECK_API_KEY: Optional[str] = None
IPQUALITYSCORE_API_KEY: Optional[str] = None

# ── DATA MODELS ───────────────────────────────────────────────────────────────

@dataclass
class GuildVerificationConfig:
    """Represents server-specific verification routing and role configurations stored in SQLite."""
    guild_id: int
    verified_role_id: int
    unverified_role_id: Optional[int]
    quarantine_role_id: Optional[int]
    log_channel_id: int
    setup_by_id: int
    updated_at: str


@dataclass
class VerificationTelemetry:
    """Carries session audit metadata through the 4-layer screening pipeline."""
    discord_id: int
    discord_age_days: int
    roblox_id: Optional[int] = None
    roblox_username: Optional[str] = None
    roblox_age_days: int = 0
    ip_address: Optional[str] = None
    browser_hash: Optional[str] = None
    is_vpn: bool = False
    risk_score: int = 0
    flags: List[str] = field(default_factory=list)
