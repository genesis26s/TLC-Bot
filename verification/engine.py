import discord
from typing import Tuple
from datetime import datetime, timezone
from verification.config import MIN_DISCORD_AGE_DAYS, MIN_ROBLOX_AGE_DAYS, VerificationTelemetry
from verification.database import VerificationDatabase
from verification.roblox_api import RobloxAuditEngine
from verification.threat_intel import ThreatIntelEngine

class VerificationPipelineEngine:
    def __init__(self, db: VerificationDatabase):
        self.db = db

    async def run_pipeline(
        self, 
        member: discord.Member, 
        roblox_username: str, 
        browser_hash: str, 
        ip_address: str
    ) -> Tuple[bool, str, VerificationTelemetry]:

        telemetry = VerificationTelemetry(
            discord_id=member.id,
            discord_age_days=(datetime.now(timezone.utc) - member.created_at).days,
            browser_hash=browser_hash,
            ip_address=ip_address
        )

        # ── LAYER 1: DISCORD PROFILE AUDIT ──────────────────────────────────
        if telemetry.discord_age_days < MIN_DISCORD_AGE_DAYS:
            telemetry.flags.append(f"Discord Account Age ({telemetry.discord_age_days}d) below threshold ({MIN_DISCORD_AGE_DAYS}d)")
            return False, "Layer 1 Denied: Discord account is too new.", telemetry

        if member.avatar is None:
            telemetry.risk_score += 15
            telemetry.flags.append("Default Discord avatar detected")

        # ── LAYER 2: HARDWARE FINGERPRINT CROSS-AUDIT ────────────────────────
        matched_alt_discord_id = self.db.find_alt_by_fingerprint(browser_hash)
        if matched_alt_discord_id and matched_alt_discord_id != member.id:
            self.db.record_alt_linkage(matched_alt_discord_id, member.id, "Hardware Browser Fingerprint Match")
            telemetry.flags.append(f"Matched hardware fingerprint with user: <@{matched_alt_discord_id}>")
            return False, f"Layer 2 Denied: Alt account detected via hardware fingerprint.", telemetry

        # ── LAYER 3: REAL-TIME THREAT & ANTI-VPN SCREENING ───────────────────
        is_vpn, threat_risk, vpn_details = await ThreatIntelEngine.check_ip(ip_address)
        telemetry.is_vpn = is_vpn
        telemetry.risk_score += threat_risk

        if is_vpn:
            telemetry.flags.append(f"VPN / Proxy Detected ({vpn_details})")
            return False, "Layer 3 Denied: Active VPN or Proxy detected.", telemetry

        # ── LAYER 4: ROBLOX ACCOUNT & DUPLICATE BIND AUDIT ───────────────────
        success, r_msg, r_data = await RobloxAuditEngine.fetch_user_data(roblox_username)
        if not success:
            return False, f"Layer 4 Denied: {r_msg}", telemetry

        telemetry.roblox_id = r_data["resolved_id"]
        telemetry.roblox_username = r_data["resolved_name"]
        telemetry.roblox_age_days = RobloxAuditEngine.calculate_account_age(r_data["created"])

        if telemetry.roblox_age_days < MIN_ROBLOX_AGE_DAYS:
            telemetry.flags.append(f"Roblox Account Age ({telemetry.roblox_age_days}d) below threshold ({MIN_ROBLOX_AGE_DAYS}d)")
            return False, "Layer 4 Denied: Roblox account is too new.", telemetry

        if r_data.get("isBanned", False):
            telemetry.flags.append("Target Roblox account is banned on Roblox")
            return False, "Layer 4 Denied: Roblox account is banned.", telemetry

        # Check Duplicate Roblox Account Link
        existing_linked_id = self.db.find_alt_by_roblox_id(telemetry.roblox_id)
        if existing_linked_id and existing_linked_id != member.id:
            self.db.record_alt_linkage(existing_linked_id, member.id, "Attempted duplicate Roblox account linkage")
            telemetry.flags.append(f"Roblox account already registered to user: <@{existing_linked_id}>")
            return False, "Layer 4 Denied: Roblox account is already linked to another user.", telemetry

        # Pipeline Passed — Persist into Master Verified Database
        self.db.save_verified_user(
            discord_id=member.id,
            roblox_user=telemetry.roblox_username,
            roblox_id=telemetry.roblox_id,
            ip=ip_address,
            hash_val=browser_hash,
            risk=telemetry.risk_score,
            is_vpn=telemetry.is_vpn
        )

        return True, "All Security Layers Cleared Successfully.", telemetry
