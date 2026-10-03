import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_NAME = "bot_data.db"

def get_connection() -> sqlite3.Connection:
    """
    Creates an anti-corruption connection to SQLite:
    - WAL Mode enabled for high-concurrency safety
    - 5000ms busy timeout to prevent database lock errors
    - Dictionary row mapping for clean access
    """
    conn = sqlite3.connect(DB_NAME, timeout=5.0)
    conn.row_factory = sqlite3.Row
    
    # Configure PRAGMAs for high reliability & corruption prevention
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.close()
    
    return conn

def init_database():
    """Initializes all required database tables safely."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()

            # Guild Settings Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    welcome_channel INTEGER,
                    goodbye_channel INTEGER,
                    mod_log_id INTEGER,
                    alert_channel INTEGER,
                    verify_channel INTEGER,
                    verified_role INTEGER,
                    lockdown_active INTEGER DEFAULT 0
                )
            """)

            # Welcome Config Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS welcome_config (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    title TEXT,
                    description TEXT,
                    footer TEXT,
                    color INTEGER,
                    thumbnail_url TEXT,
                    image_url TEXT
                )
            """)

            # Mod Actions Log Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mod_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    mod_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    target_id INTEGER,
                    reason TEXT,
                    extra_data TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Active Mutes Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mutes (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    mod_id INTEGER NOT NULL,
                    reason TEXT,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            # Warnings Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    mod_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    warned_at TEXT NOT NULL
                )
            """)

            # Security Events Audit Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    user_id INTEGER,
                    details TEXT,
                    severity TEXT NOT NULL,
                    logged_at TEXT NOT NULL
                )
            """)

            # Captcha Verification Storage Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS verifications (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
    finally:
        conn.close()

# ── Guild Settings Helpers ──────────────────────────────────────────────────

def get_guild_settings(guild_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def upsert_guild_settings(guild_id: int, **kwargs):
    if not kwargs:
        return
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            
            fields = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [guild_id]
            
            cursor.execute(f"UPDATE guild_settings SET {fields} WHERE guild_id = ?", values)
    finally:
        conn.close()

# ── Welcome System Helpers ──────────────────────────────────────────────────

def get_welcome_config(guild_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM welcome_config WHERE guild_id = ?", (guild_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def upsert_welcome_config(guild_id: int, **kwargs):
    if not kwargs:
        return
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO welcome_config (guild_id) VALUES (?)", (guild_id,))
            
            fields = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [guild_id]
            
            cursor.execute(f"UPDATE welcome_config SET {fields} WHERE guild_id = ?", values)
    finally:
        conn.close()

# ── Moderation Helpers ──────────────────────────────────────────────────────

def log_mod_action(guild_id: int, mod_id: int, action: str, target_id: Optional[int], reason: str, extra_data: Optional[str] = None):
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT INTO mod_logs (guild_id, mod_id, action, target_id, reason, extra_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (guild_id, mod_id, action, target_id, reason, extra_data, now))
    finally:
        conn.close()

def add_mute(guild_id: int, user_id: int, mod_id: int, reason: str, expires_at: str):
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO mutes (guild_id, user_id, mod_id, reason, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    mod_id = excluded.mod_id,
                    reason = excluded.reason,
                    expires_at = excluded.expires_at
            """, (guild_id, user_id, mod_id, reason, expires_at))
    finally:
        conn.close()

def remove_mute(guild_id: int, user_id: int):
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    finally:
        conn.close()

def get_expired_mutes() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("SELECT * FROM mutes WHERE expires_at <= ?", (now,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def add_warning(guild_id: int, user_id: int, mod_id: int, reason: str) -> int:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT INTO warnings (guild_id, user_id, mod_id, reason, warned_at)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, user_id, mod_id, reason, now))
            return cursor.lastrowid
    finally:
        conn.close()

def get_warnings(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC", (guild_id, user_id))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def clear_warnings(guild_id: int, user_id: int) -> int:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            return cursor.rowcount
    finally:
        conn.close()

# ── Security & Verification Helpers ───────────────────────────────────────

def log_security_event(guild_id: int, event_type: str, user_id: Optional[int], details: str, severity: str = "medium"):
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT INTO security_events (guild_id, event_type, user_id, details, severity, logged_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, event_type, user_id, details, severity, now))
    finally:
        conn.close()

def get_security_events(guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM security_events WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (guild_id, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def create_verification(guild_id: int, user_id: int, code: str, expires_at: str):
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO verifications (guild_id, user_id, code, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    code = excluded.code,
                    expires_at = excluded.expires_at
            """, (guild_id, user_id, code, expires_at))
    finally:
        conn.close()

def verify_code(guild_id: int, user_id: int, input_code: str) -> bool:
    conn = get_connection()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM verifications 
            WHERE guild_id = ? AND user_id = ? AND code = ? AND expires_at > ?
        """, (guild_id, user_id, input_code, now))
        
        row = cursor.fetchone()
        if row:
            with conn:
                cursor.execute("DELETE FROM verifications WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            return True
        return False
    finally:
        conn.close()

        # ── Friendly System Helpers ────────────────────────────────────────────────

def init_friendly_db():
    """Creates the friendly system tables. Call once at bot startup."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS friendly_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    role_1_id INTEGER,
                    role_2_id INTEGER,
                    updated_at TEXT
                )
            """)
    finally:
        conn.close()

# Auto-init on import (safe to call multiple times — uses IF NOT EXISTS)
init_friendly_db()


def get_friendly_settings(guild_id: int) -> Optional[Dict[str, Any]]:
    """Returns the friendly config for a guild, or None if not set up."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM friendly_settings WHERE guild_id = ?",
            (guild_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_friendly_settings(guild_id: int, **kwargs):
    """Update or insert friendly settings for a guild."""
    if not kwargs:
        return
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            # Ensure row exists
            cursor.execute(
                "INSERT OR IGNORE INTO friendly_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            # Update fields
            kwargs["updated_at"] = datetime.utcnow().isoformat()
            fields = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [guild_id]
            cursor.execute(
                f"UPDATE friendly_settings SET {fields} WHERE guild_id = ?",
                values,
            )
    finally:
        conn.close()
        
        # ── Leaderboard System Helpers ───────────────────────────────────────────────

def init_leaderboard_db():
    """Creates the leaderboard tables. Call once at bot startup."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            # Per-message log (optional, useful for future "message of the day" etc.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_message_log_lookup
                ON message_log (guild_id, user_id, message_date)
            """)
            # All-time counter (fast reads)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_counts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_message_at TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
    finally:
        conn.close()


# Auto-init
init_leaderboard_db()


def increment_message_count(guild_id: int, user_id: int):
    """Increment all-time counter + log the message."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            now = datetime.utcnow()
            date_str = now.strftime("%Y-%m-%d")
            created_at = now.isoformat()

            # All-time counter (upsert)
            cursor.execute("""
                INSERT INTO message_counts (guild_id, user_id, count, last_message_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    count = count + 1,
                    last_message_at = excluded.last_message_at
            """, (guild_id, user_id, created_at))

            # Per-message log
            cursor.execute("""
                INSERT INTO message_log (guild_id, user_id, channel_id, message_date, created_at)
                VALUES (?, ?, 0, ?, ?)
            """, (guild_id, user_id, date_str, created_at))
    except Exception:
        # Don't let DB errors kill messages
        pass
    finally:
        conn.close()


def get_all_time_leaderboard(guild_id: int, limit: int = 10) -> list:
    """Returns [(user_id, count), ...] sorted desc."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, count
            FROM message_counts
            WHERE guild_id = ? AND count > 0
            ORDER BY count DESC
            LIMIT ?
        """, (guild_id, limit))
        return cursor.fetchall()
    finally:
        conn.close()


def get_user_all_time_count(guild_id: int, user_id: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count FROM message_counts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_user_rank(guild_id: int, user_id: int) -> int:
    """Returns the user's rank (1 = top). 0 if not ranked."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count FROM message_counts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        if not row or row[0] == 0:
            return 0
        my_count = row[0]
        cursor.execute(
            "SELECT COUNT(*) FROM message_counts WHERE guild_id = ? AND count > ?",
            (guild_id, my_count),
        )
        higher = cursor.fetchone()[0]
        return higher + 1
    finally:
        conn.close()


def get_period_leaderboard(guild_id: int, days: int, limit: int = 10) -> list:
    """
    Returns top users for a given period.
    days = 1 (daily), 7 (weekly), 30 (monthly), 365 (yearly).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Build the date filter: messages with message_date >= today - days
        cutoff = (datetime.utcnow() - __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT user_id, COUNT(*) as cnt
            FROM message_log
            WHERE guild_id = ? AND message_date >= ?
            GROUP BY user_id
            ORDER BY cnt DESC
            LIMIT ?
        """, (guild_id, cutoff, limit))
        return cursor.fetchall()
    finally:
        conn.close()


def get_user_period_count(guild_id: int, user_id: int, days: int) -> int:
    """Returns how many messages a user sent in the last N days."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cutoff = (datetime.utcnow() - __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT COUNT(*) FROM message_log
            WHERE guild_id = ? AND user_id = ? AND message_date >= ?
        """, (guild_id, user_id, cutoff))
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
