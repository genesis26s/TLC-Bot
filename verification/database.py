import sqlite3
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timezone
from verification.config import DB_FILE, GuildVerificationConfig

class VerificationDatabase:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Server Configuration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS web_verify_config (
                    guild_id INTEGER PRIMARY KEY,
                    verified_role_id INTEGER NOT NULL,
                    unverified_role_id INTEGER,
                    quarantine_role_id INTEGER,
                    log_channel_id INTEGER NOT NULL,
                    setup_by_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Verification Web Tokens
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS verification_tokens (
                    token TEXT PRIMARY KEY,
                    discord_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # Verified Users Master Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS verified_users (
                    discord_id INTEGER PRIMARY KEY,
                    roblox_username TEXT NOT NULL,
                    roblox_id INTEGER NOT NULL,
                    ip_address TEXT,
                    browser_hash TEXT NOT NULL,
                    risk_score INTEGER DEFAULT 0,
                    is_vpn INTEGER DEFAULT 0,
                    verified_at TEXT NOT NULL
                )
            """)

            # Anti-Alt Linkages Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alt_linkages (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    primary_discord_id INTEGER NOT NULL,
                    alt_discord_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def get_config(self, guild_id: int) -> Optional[GuildVerificationConfig]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM web_verify_config WHERE guild_id = ?", (guild_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return GuildVerificationConfig(
                guild_id=row["guild_id"],
                verified_role_id=row["verified_role_id"],
                unverified_role_id=row["unverified_role_id"],
                quarantine_role_id=row["quarantine_role_id"],
                log_channel_id=row["log_channel_id"],
                setup_by_id=row["setup_by_id"],
                updated_at=row["updated_at"]
            )

    def save_config(self, config: GuildVerificationConfig):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO web_verify_config 
                (guild_id, verified_role_id, unverified_role_id, quarantine_role_id, log_channel_id, setup_by_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                config.guild_id, config.verified_role_id, config.unverified_role_id,
                config.quarantine_role_id, config.log_channel_id, config.setup_by_id, config.updated_at
            ))
            conn.commit()

    def create_token(self, token: str, discord_id: int, guild_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO verification_tokens (token, discord_id, guild_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (token, discord_id, guild_id, now_iso))
            conn.commit()

    def consume_token(self, token: str) -> Optional[Tuple[int, int]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT discord_id, guild_id FROM verification_tokens WHERE token = ?", (token,))
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute("DELETE FROM verification_tokens WHERE token = ?", (token,))
            conn.commit()
            return row["discord_id"], row["guild_id"]

    def find_alt_by_fingerprint(self, browser_hash: str) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT discord_id FROM verified_users WHERE browser_hash = ?", (browser_hash,))
            row = cursor.fetchone()
            return row["discord_id"] if row else None

    def find_alt_by_roblox_id(self, roblox_id: int) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT discord_id FROM verified_users WHERE roblox_id = ?", (roblox_id,))
            row = cursor.fetchone()
            return row["discord_id"] if row else None

    def record_alt_linkage(self, primary_id: int, alt_id: int, reason: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO alt_linkages (primary_discord_id, alt_discord_id, reason, detected_at)
                VALUES (?, ?, ?, ?)
            """, (primary_id, alt_id, reason, now_iso))
            conn.commit()

    def save_verified_user(self, discord_id: int, roblox_user: str, roblox_id: int, ip: str, hash_val: str, risk: int, is_vpn: bool):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO verified_users 
                (discord_id, roblox_username, roblox_id, ip_address, browser_hash, risk_score, is_vpn, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (discord_id, roblox_user, roblox_id, ip, hash_val, risk, 1 if is_vpn else 0, now_iso))
            conn.commit()
