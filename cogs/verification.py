import os
import secrets
import time
import datetime
import logging
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from verification.config import WEB_SERVER_URL, GuildVerificationConfig
from verification.database import VerificationDatabase

logger = logging.getLogger("TLCBot.VerificationCog")

# Shared Database Instance
db = VerificationDatabase()

RISK_THRESHOLD_QUARANTINE = 75
RISK_THRESHOLD_REVIEW = 45

class PersistentVerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Identity", 
        style=discord.ButtonStyle.green, 
        custom_id="tlc_web_verify_btn_persistent", 
        emoji="🛡️"
    )
    async def verify_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        cog = interaction.client.get_cog("VerificationCog")
        if not cog:
            await interaction.followup.send("❌ Verification subsystem offline.", ephemeral=True)
            return

        token = cog.generate_verification_token(interaction.user.id, interaction.guild_id)
        web_url = os.getenv("VERIFICATION_WEB_URL", WEB_SERVER_URL)
        verify_link = f"{web_url.rstrip('/')}/verify/{token}"

        embed = discord.Embed(
            title="🔒 TLC Security Verification",
            description=(
                "Click the link below to complete verification through our secure portal.\n\n"
                "**Note:** This link is unique to you and will expire in **10 minutes**."
            ),
            color=discord.Color.blue()
        )
        embed.add_field(name="Verification Link", value=f"[👉 Complete Verification]({verify_link})", inline=False)
        embed.set_footer(text="Touchline Competitive • Security System")

        await interaction.followup.send(embed=embed, ephemeral=True)


class VerificationCog(commands.Cog, name="VerificationCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_tokens = {}
        self.bot.add_view(PersistentVerifyButton())

    def generate_verification_token(self, user_id: int, guild_id: int) -> str:
        now = time.time()
        self.pending_tokens = {
            k: v for k, v in self.pending_tokens.items()
            if now - v["created_at"] < 600
        }
        token = secrets.token_urlsafe(24)
        self.pending_tokens[token] = {
            "user_id": user_id,
            "guild_id": guild_id,
            "created_at": now
        }
        db.create_token(token, user_id, guild_id)
        return token

    def validate_token(self, token: str) -> Optional[dict]:
        token_data = self.pending_tokens.get(token)
        if not token_data:
            # Check DB backup token
            consumed = db.consume_token(token)
            if consumed:
                return {"user_id": consumed[0], "guild_id": consumed[1], "created_at": time.time()}
            return None

        if time.time() - token_data["created_at"] > 600:
            del self.pending_tokens[token]
            return None

        del self.pending_tokens[token]
        return token_data

    async def calculate_risk_score(
        self, member: discord.Member, roblox_data: dict, fingerprint_hash: str, client_ip: str
    ) -> tuple[int, list[str]]:
        risk_score = 0
        risk_factors = []

        discord_age_days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        if discord_age_days < 3:
            risk_score += 40
            risk_factors.append(f"Discord account extremely new ({discord_age_days}d old)")
        elif discord_age_days < 14:
            risk_score += 20
            risk_factors.append(f"Discord account new ({discord_age_days}d old)")

        roblox_created_str = roblox_data.get("created")
        if roblox_created_str:
            try:
                roblox_created = datetime.datetime.fromisoformat(roblox_created_str.replace("Z", "+00:00"))
                roblox_age_days = (datetime.datetime.now(datetime.timezone.utc) - roblox_created).days
                if roblox_age_days < 7:
                    risk_score += 35
                    risk_factors.append(f"Roblox account extremely new ({roblox_age_days}d old)")
                elif roblox_age_days < 30:
                    risk_score += 15
                    risk_factors.append(f"Roblox account new ({roblox_age_days}d old)")
            except Exception as e:
                logger.warning(f"Error parsing Roblox creation date: {e}")

        if not member.avatar:
            risk_score += 10
            risk_factors.append("Default Discord avatar")

        existing_roblox = db.get_verification_by_roblox_id(str(roblox_data.get("id")))
        if existing_roblox and existing_roblox.get("discord_id") != str(member.id):
            risk_score += 50
            risk_factors.append(f"Roblox ID linked to another Discord user ({existing_roblox.get('discord_id')})")

        existing_fp = db.get_verification_by_fingerprint(fingerprint_hash)
        if existing_fp and existing_fp.get("discord_id") != str(member.id):
            risk_score += 30
            risk_factors.append("Browser fingerprint associated with another account")

        return min(risk_score, 100), risk_factors

    async def complete_verification(
        self, token: str, roblox_username: str, roblox_id: int, fingerprint_hash: str, client_ip: str
    ) -> dict:
        token_data = self.validate_token(token)
        if not token_data:
            return {"success": False, "message": "Invalid or expired verification session token."}

        guild = self.bot.get_guild(token_data["guild_id"])
        if not guild:
            return {"success": False, "message": "Target server not found."}

        member = guild.get_member(token_data["user_id"])
        if not member:
            try:
                member = await guild.fetch_member(token_data["user_id"])
            except Exception:
                return {"success": False, "message": "Member is no longer in the server."}

        roblox_payload = {
            "id": roblox_id,
            "username": roblox_username,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        risk_score, risk_factors = await self.calculate_risk_score(member, roblox_payload, fingerprint_hash, client_ip)

        db.save_verification_record(
            discord_id=str(member.id),
            guild_id=str(guild.id),
            roblox_id=str(roblox_id),
            roblox_username=roblox_username,
            fingerprint_hash=fingerprint_hash,
            client_ip=client_ip,
            risk_score=risk_score
        )

        config = db.get_config(guild.id)
        verified_role = guild.get_role(config.verified_role_id) if config else discord.utils.get(guild.roles, name="Verified")
        unverified_role = guild.get_role(config.unverified_role_id) if config and config.unverified_role_id else discord.utils.get(guild.roles, name="Unverified")
        quarantine_role = guild.get_role(config.quarantine_role_id) if config and config.quarantine_role_id else discord.utils.get(guild.roles, name="Quarantine")

        action_taken = "Verified"

        if risk_score >= RISK_THRESHOLD_QUARANTINE and quarantine_role:
            await member.add_roles(quarantine_role, reason=f"High Risk Verification Score ({risk_score})")
            if unverified_role and unverified_role in member.roles:
                await member.remove_roles(unverified_role)
            action_taken = "Quarantined"
        else:
            if verified_role:
                await member.add_roles(verified_role, reason="Web Verification Completed")
            if unverified_role and unverified_role in member.roles:
                await member.remove_roles(unverified_role)

            try:
                await member.edit(nick=roblox_username, reason="Verification Nickname Update")
            except discord.Forbidden:
                logger.warning(f"Cannot update nickname for {member.display_name}")

        await self.send_security_log(guild, member, roblox_username, roblox_id, risk_score, risk_factors, action_taken)

        return {
            "success": True,
            "message": f"Verification completed successfully. Status: {action_taken}",
            "risk_score": risk_score,
            "action_taken": action_taken
        }

    async def send_security_log(
        self, guild: discord.Guild, member: discord.Member, roblox_username: str, roblox_id: int, risk_score: int, risk_factors: list[str], action_taken: str
    ):
        config = db.get_config(guild.id)
        log_channel = guild.get_channel(config.log_channel_id) if config else discord.utils.get(guild.text_channels, name="verification-logs")
        if not log_channel:
            return

        color = discord.Color.green() if action_taken == "Verified" else discord.Color.red()
        embed = discord.Embed(
            title="🛡️ Verification Security Audit",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Roblox Account", value=f"[{roblox_username}](https://www.roblox.com/users/{roblox_id}/profile)", inline=True)
        embed.add_field(name="Risk Score", value=f"**{risk_score}/100**", inline=True)
        embed.add_field(name="Action Taken", value=f"`{action_taken}`", inline=True)

        if risk_factors:
            embed.add_field(name="Risk Flags", value="\n".join([f"• {f}" for f in risk_factors]), inline=False)

        embed.set_thumbnail(url=member.display_avatar.url)
        await log_channel.send(embed=embed)

    @app_commands.command(name="verification_setup", description="Deploy verification panel, roles, and log settings.")
    @app_commands.checks.has_permissions(administrator=True)
    async def verification_setup(
        self,
        interaction: discord.Interaction,
        portal_channel: discord.TextChannel,
        verified_role: discord.Role,
        logging_channel: discord.TextChannel,
        unverified_role: Optional[discord.Role] = None,
        quarantine_role: Optional[discord.Role] = None
    ):
        await interaction.response.defer(ephemeral=True)

        config = GuildVerificationConfig(
            guild_id=interaction.guild_id,
            verified_role_id=verified_role.id,
            unverified_role_id=unverified_role.id if unverified_role else None,
            quarantine_role_id=quarantine_role.id if quarantine_role else None,
            log_channel_id=logging_channel.id,
            setup_by_id=interaction.user.id,
            updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        db.save_config(config)

        embed = discord.Embed(
            title="🛡️ SERVER SECURITY & VERIFICATION PORTAL",
            description=(
                f"Welcome to **{interaction.guild.name}**!\n\n"
                f"To access server channels, verify your profile by clicking the button below."
            ),
            color=discord.Color.brand_green()
        )
        await portal_channel.send(embed=embed, view=PersistentVerifyButton())
        await interaction.followup.send("✅ Verification system successfully configured!", ephemeral=True)

    @app_commands.command(name="verify_info", description="Look up verification records for a user.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def verify_info(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        data = db.get_verification_record(str(member.id))
        if not data:
            return await interaction.followup.send(f"❌ No verification record found for {member.mention}.", ephemeral=True)

        embed = discord.Embed(
            title=f"📋 Verification Record: {member.display_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Discord ID", value=f"`{data.get('discord_id')}`", inline=True)
        embed.add_field(name="Roblox Username", value=f"`{data.get('roblox_username')}`", inline=True)
        embed.add_field(name="Roblox ID", value=f"`{data.get('roblox_id')}`", inline=True)
        embed.add_field(name="Risk Score", value=f"`{data.get('risk_score', 0)}/100`", inline=True)
        embed.add_field(name="Verified At", value=f"`{data.get('verified_at', 'N/A')}`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
