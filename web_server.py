from quart import Quart, render_template, request, jsonify
import discord
import asyncio
import logging
from typing import Optional

from verification.config import WEB_SERVER_URL
from verification.database import VerificationDatabase
from verification.engine import VerificationPipelineEngine

logger = logging.getLogger("TLCBot.WebServer")

app = Quart(__name__)
db = VerificationDatabase()
pipeline = VerificationPipelineEngine(db)

# References the Discord Bot instance initialized in your main entrypoint
bot_instance: Optional[discord.Client] = None

def set_bot_instance(bot: discord.Client):
    global bot_instance
    bot_instance = bot

@app.route('/verify/<token>', methods=['GET'])
async def verify_page(token: str):
    return await render_template('verify.html')

@app.route('/api/process-verification', methods=['POST'])
async def process_verification():
    data = await request.get_json()
    token = data.get('token')
    roblox_username = data.get('roblox_username')
    browser_hash = data.get('browser_hash')

    if not token or not roblox_username or not browser_hash:
        return jsonify({"success": False, "message": "Missing required verification data."}), 400

    # Consume token from DB
    session_data = db.consume_token(token)
    if not session_data:
        return jsonify({"success": False, "message": "Invalid or expired session link. Re-click 'Verify Identity' in Discord."}), 400

    discord_id, guild_id = session_data

    if not bot_instance:
        return jsonify({"success": False, "message": "Bot engine unavailable. Try again shortly."}), 500

    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({"success": False, "message": "Guild reference lost."}), 400

    member = guild.get_member(discord_id) or await guild.fetch_member(discord_id)
    if not member:
        return jsonify({"success": False, "message": "Discord member not found in target server."}), 404

    # Extract Client Remote IP
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()

    # Run full 4-Layer Verification Pipeline
    passed, reason, telemetry = await pipeline.run_pipeline(member, roblox_username, browser_hash, ip_address)

    # Fetch Server Configuration
    config = db.get_config(guild_id)
    log_channel = guild.get_channel(config.log_channel_id) if config else None

    if passed:
        # Assign Verified Role & Remove Unverified Role
        try:
            verified_role = guild.get_role(config.verified_role_id)
            if verified_role:
                await member.add_roles(verified_role, reason="Passed TLC Web Verification")

            if config.unverified_role_id:
                unverified_role = guild.get_role(config.unverified_role_id)
                if unverified_role:
                    await member.remove_roles(unverified_role, reason="Passed TLC Web Verification")
        except discord.Forbidden:
            logger.error("Missing permissions to modify roles.")

        # Send Log Entry to Designated Channel
        if log_channel:
            embed = discord.Embed(
                title="✅ Member Verified Successfully",
                color=discord.Color.green()
            )
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Roblox Username", value=telemetry.roblox_username, inline=True)
            embed.add_field(name="Roblox ID", value=str(telemetry.roblox_id), inline=True)
            embed.add_field(name="Risk Score", value=f"{telemetry.risk_score}/100", inline=True)
            await log_channel.send(embed=embed)

        return jsonify({"success": True, "message": "Verification completed successfully!"})

    else:
        # Handle Security Failure / Alt / VPN Flag
        if config and config.quarantine_role_id:
            try:
                quarantine_role = guild.get_role(config.quarantine_role_id)
                if quarantine_role:
                    await member.add_roles(quarantine_role, reason=f"Failed Verification: {reason}")
            except discord.Forbidden:
                pass

        # Send Security Warning Log Entry
        if log_channel:
            embed = discord.Embed(
                title="🚨 Security Flag / Verification Denied",
                description=f"**Reason:** {reason}",
                color=discord.Color.red()
            )
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Attempted Roblox", value=roblox_username, inline=True)
            embed.add_field(name="IP Address", value=f"`{ip_address}`", inline=True)
            
            if telemetry.flags:
                embed.add_field(name="Flags Triggered", value="\n".join([f"• {f}" for f in telemetry.flags]), inline=False)
                
            await log_channel.send(embed=embed)

        return jsonify({"success": False, "message": reason})

async def run_web_server():
    await app.run_task(host="0.0.0.0", port=30088)
