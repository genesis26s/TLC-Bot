import discord
from discord.ext import commands
from discord import app_commands
from cogs.security_utils import (
    get_guild_security_config,
    save_guild_security_config,
    log_security_event,
    DEFAULT_GUILD_SECURITY
)

class SecurityConfigCog(commands.Cog):
    """Unified administration cog for managing Anti-Nuke, Anti-Spam, Anti-Raid & Whitelists."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    security_group = app_commands.Group(name="security", description="TLC Security System Management")
    antispam_group = app_commands.Group(name="antispam", description="Anti-Spam Configuration & Exemptions", parent=security_group)

    # 1. STATUS COMMAND
    @security_group.command(name="status", description="Display current security protection status and configurations.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def security_status(self, interaction: discord.Interaction):
        cfg = get_guild_security_config(interaction.guild_id)

        embed = discord.Embed(
            title=f"🛡️ Security Status — {interaction.guild.name}",
            description="Overview of real-time server automated defenses.",
            color=discord.Color.dark_theme()
        )

        sys_status = "🟢 Enabled" if cfg.get("enabled") else "🔴 Disabled"
        lock_status = "🔒 ACTIVE" if cfg.get("lockdown") else "🔓 Normal"

        embed.add_field(name="Global Protection", value=sys_status, inline=True)
        embed.add_field(name="Lockdown State", value=lock_status, inline=True)
        
        log_ch = f"<#{cfg['security_log_channel_id']}>" if cfg.get("security_log_channel_id") else "Default Log System"
        embed.add_field(name="Log Channel", value=log_ch, inline=True)

        # Module Details
        an = cfg["anti_nuke"]
        an_str = f"Status: {'🟢' if an['enabled'] else '🔴'}\nAction: `{an['action'].upper()}`\nDel Limit: `{an['channel_delete_limit']} / {an['channel_delete_window']}s`"
        embed.add_field(name="Anti-Nuke", value=an_str, inline=False)

        asp = cfg["anti_spam"]
        asp_str = (
            f"Status: {'🟢' if asp['enabled'] else '🔴'}\n"
            f"Action: `{asp['action'].upper()}`\n"
            f"Flood Limit: `{asp['message_limit']} msg / {asp['message_window']}s`\n"
            f"Dup Limit: `{asp['duplicate_limit']} / {asp['duplicate_window']}s` | Mentions: `{asp['mention_limit']}` | Links: `{asp['link_limit']}`"
        )
        embed.add_field(name="Anti-Spam", value=asp_str, inline=False)

        ar = cfg["anti_raid"]
        ar_str = f"Status: {'🟢' if ar['enabled'] else '🔴'}\nAction: `{ar['action'].upper()}`\nJoin Limit: `{ar['join_limit']} joins / {ar['join_window']}s`"
        embed.add_field(name="Anti-Raid", value=ar_str, inline=False)

        wl_users = len(cfg.get("whitelist_users", []))
        wl_roles = len(cfg.get("whitelist_roles", []))
        exempt_ch = len(cfg.get("exempt_channels", []))
        embed.add_field(name="Whitelists & Exemptions", value=f"Users: `{wl_users}` | Roles: `{wl_roles}` | Exempt Channels: `{exempt_ch}`", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 2. ENABLE / DISABLE GLOBAL SECURITY MODULES
    @security_group.command(name="toggle", description="Enable or disable a specific security module.")
    @app_commands.choices(module=[
        app_commands.Choice(name="Global System", value="global"),
        app_commands.Choice(name="Anti-Nuke", value="anti_nuke"),
        app_commands.Choice(name="Anti-Spam", value="anti_spam"),
        app_commands.Choice(name="Anti-Raid", value="anti_raid")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle_security(self, interaction: discord.Interaction, module: str, enabled: bool):
        cfg = get_guild_security_config(interaction.guild_id)

        if module == "global":
            cfg["enabled"] = enabled
        elif module in cfg:
            cfg[module]["enabled"] = enabled

        save_guild_security_config(interaction.guild_id, cfg)

        state_str = "ENABLED 🟢" if enabled else "DISABLED 🔴"
        await interaction.response.send_message(f"✅ Security module `{module.upper()}` is now **{state_str}**.", ephemeral=True)

        await log_security_event(
            interaction.guild,
            title="Security Configuration Modified",
            description=f"Administrator {interaction.user.mention} updated security settings.",
            fields=[
                ("Module Changed", module.upper(), True),
                ("New Status", state_str, True)
            ],
            color=discord.Color.blue()
        )

    # 3. ANTI-SPAM DETAILED CONFIGURATION (/security antispam config)
    @antispam_group.command(name="config", description="Configure Anti-Spam penalty actions, thresholds, and windows.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Delete Only", value="delete"),
        app_commands.Choice(name="Warn User", value="warn"),
        app_commands.Choice(name="Timeout User", value="timeout"),
        app_commands.Choice(name="Kick User", value="kick"),
        app_commands.Choice(name="Ban User", value="ban")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def antispam_config(
        self,
        interaction: discord.Interaction,
        enabled: bool = None,
        action: str = None,
        message_limit: int = None,
        message_window: int = None,
        duplicate_limit: int = None,
        duplicate_window: int = None,
        mention_limit: int = None,
        link_limit: int = None,
        character_limit: int = None,
        timeout_duration_minutes: int = None
    ):
        cfg = get_guild_security_config(interaction.guild_id)
        asp = cfg["anti_spam"]

        changes = []

        if enabled is not None:
            asp["enabled"] = enabled
            changes.append(f"• **Enabled**: `{enabled}`")

        if action is not None:
            asp["action"] = action
            changes.append(f"• **Action**: `{action.upper()}`")

        if message_limit is not None and message_limit > 0:
            asp["message_limit"] = message_limit
            changes.append(f"• **Message Limit**: `{message_limit} msgs`")

        if message_window is not None and message_window > 0:
            asp["message_window"] = message_window
            changes.append(f"• **Message Window**: `{message_window}s`")

        if duplicate_limit is not None and duplicate_limit > 0:
            asp["duplicate_limit"] = duplicate_limit
            changes.append(f"• **Duplicate Limit**: `{duplicate_limit} msgs`")

        if duplicate_window is not None and duplicate_window > 0:
            asp["duplicate_window"] = duplicate_window
            changes.append(f"• **Duplicate Window**: `{duplicate_window}s`")

        if mention_limit is not None and mention_limit > 0:
            asp["mention_limit"] = mention_limit
            changes.append(f"• **Mention Limit**: `{mention_limit} mentions`")

        if link_limit is not None and link_limit > 0:
            asp["link_limit"] = link_limit
            changes.append(f"• **Link Limit**: `{link_limit} links`")

        if character_limit is not None and character_limit > 0:
            asp["character_limit"] = character_limit
            changes.append(f"• **Character Limit**: `{character_limit} chars`")

        if timeout_duration_minutes is not None and timeout_duration_minutes > 0:
            asp["timeout_duration_minutes"] = timeout_duration_minutes
            changes.append(f"• **Timeout Duration**: `{timeout_duration_minutes}m`")

        if not changes:
            return await interaction.response.send_message("⚠️ No configuration parameters were specified. Pass parameters to update Anti-Spam settings.", ephemeral=True)

        save_guild_security_config(interaction.guild_id, cfg)

        embed = discord.Embed(
            title="⚡ Anti-Spam Configuration Updated",
            description="\n".join(changes),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        await log_security_event(
            interaction.guild,
            title="Anti-Spam Settings Modified",
            description=f"Administrator {interaction.user.mention} updated Anti-Spam configuration.",
            fields=[("Changes Applied", "\n".join(changes), False)],
            color=discord.Color.blue()
        )

    # 4. ANTI-SPAM EXEMPTIONS (/security antispam exempt)
    @antispam_group.command(name="exempt", description="Exempt channels, roles, or users from Anti-Spam detection.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Exempt Channel", value="add_channel"),
        app_commands.Choice(name="Remove Channel Exemption", value="remove_channel"),
        app_commands.Choice(name="Exempt Role", value="add_role"),
        app_commands.Choice(name="Remove Role Exemption", value="remove_role"),
        app_commands.Choice(name="Exempt User", value="add_user"),
        app_commands.Choice(name="Remove User Exemption", value="remove_user")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def antispam_exempt(
        self,
        interaction: discord.Interaction,
        action: str,
        channel: discord.TextChannel = None,
        role: discord.Role = None,
        user: discord.User = None
    ):
        cfg = get_guild_security_config(interaction.guild_id)

        if action == "add_channel" and channel:
            if channel.id not in cfg["exempt_channels"]:
                cfg["exempt_channels"].append(channel.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"✅ Added {channel.mention} to Anti-Spam exemptions.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Channel is already exempt.", ephemeral=True)

        elif action == "remove_channel" and channel:
            if channel.id in cfg["exempt_channels"]:
                cfg["exempt_channels"].remove(channel.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"🗑️ Removed {channel.mention} from Anti-Spam exemptions.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Channel not in exemption list.", ephemeral=True)

        elif action == "add_role" and role:
            if role.id not in cfg["whitelist_roles"]:
                cfg["whitelist_roles"].append(role.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"✅ Added role {role.mention} to security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Role is already whitelisted.", ephemeral=True)

        elif action == "remove_role" and role:
            if role.id in cfg["whitelist_roles"]:
                cfg["whitelist_roles"].remove(role.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"🗑️ Removed role {role.mention} from security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Role not in whitelist.", ephemeral=True)

        elif action == "add_user" and user:
            if user.id not in cfg["whitelist_users"]:
                cfg["whitelist_users"].append(user.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"✅ Added user {user.mention} to security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ User is already whitelisted.", ephemeral=True)

        elif action == "remove_user" and user:
            if user.id in cfg["whitelist_users"]:
                cfg["whitelist_users"].remove(user.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"🗑️ Removed user {user.mention} from security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ User not in whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Invalid exemption command. Please provide the target user, role, or channel.", ephemeral=True)

    # 5. GENERAL WHITELIST COMMANDS
    @security_group.command(name="whitelist", description="Manage global security whitelists.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add User", value="add_user"),
        app_commands.Choice(name="Remove User", value="remove_user"),
        app_commands.Choice(name="Add Role", value="add_role"),
        app_commands.Choice(name="Remove Role", value="remove_role"),
        app_commands.Choice(name="List Whitelist", value="list")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def security_whitelist(self, interaction: discord.Interaction, action: str, user: discord.User = None, role: discord.Role = None):
        cfg = get_guild_security_config(interaction.guild_id)

        if action == "add_user" and user:
            if user.id not in cfg["whitelist_users"]:
                cfg["whitelist_users"].append(user.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"✅ Added {user.mention} to security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {user.mention} is already whitelisted.", ephemeral=True)

        elif action == "remove_user" and user:
            if user.id in cfg["whitelist_users"]:
                cfg["whitelist_users"].remove(user.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"🗑️ Removed {user.mention} from security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ User not found in whitelist.", ephemeral=True)

        elif action == "add_role" and role:
            if role.id not in cfg["whitelist_roles"]:
                cfg["whitelist_roles"].append(role.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"✅ Added role {role.mention} to security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Role is already whitelisted.", ephemeral=True)

        elif action == "remove_role" and role:
            if role.id in cfg["whitelist_roles"]:
                cfg["whitelist_roles"].remove(role.id)
                save_guild_security_config(interaction.guild_id, cfg)
                await interaction.response.send_message(f"🗑️ Removed role {role.mention} from security whitelist.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Role not found in whitelist.", ephemeral=True)

        elif action == "list":
            users_str = ", ".join([f"<@{uid}>" for uid in cfg["whitelist_users"]]) or "None"
            roles_str = ", ".join([f"<&{rid}>" for rid in cfg["whitelist_roles"]]) or "None"
            channels_str = ", ".join([f"<#{cid}>" for cid in cfg.get("exempt_channels", [])]) or "None"

            embed = discord.Embed(
                title=f"🛡️ Security Whitelists & Exemptions — {interaction.guild.name}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Whitelisted Users", value=users_str, inline=False)
            embed.add_field(name="Whitelisted Roles", value=roles_str, inline=False)
            embed.add_field(name="Exempt Channels", value=channels_str, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Invalid command usage.", ephemeral=True)

    # 6. LOG CHANNEL CONFIGURATION
    @security_group.command(name="log_channel", description="Configure dedicated security logging channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = get_guild_security_config(interaction.guild_id)
        cfg["security_log_channel_id"] = channel.id
        save_guild_security_config(interaction.guild_id, cfg)

        await interaction.response.send_message(f"✅ Security logs channel updated to {channel.mention}.", ephemeral=True)

    # 7. LOCKDOWN CONTROLS
    @security_group.command(name="lockdown", description="Manually enable or disable server security lockdown.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Enable Lockdown", value="enable"),
        app_commands.Choice(name="Disable Lockdown", value="disable"),
        app_commands.Choice(name="Check Status", value="status")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def security_lockdown(self, interaction: discord.Interaction, action: str):
        cfg = get_guild_security_config(interaction.guild_id)

        if action == "enable":
            cfg["lockdown"] = True
            save_guild_security_config(interaction.guild_id, cfg)

            for channel in interaction.guild.text_channels:
                try:
                    overwrites = channel.overwrites_for(interaction.guild.default_role)
                    if overwrites.send_messages is not False:
                        overwrites.send_messages = False
                        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites, reason="Security Lockdown Engaged")
                except Exception:
                    pass

            await interaction.response.send_message("🚨 **SERVER LOCKDOWN ENGAGED.** All public text channel permissions locked.", ephemeral=True)
            await log_security_event(
                interaction.guild,
                title="LOCKDOWN ENGAGED",
                description=f"Manual security lockdown was engaged by {interaction.user.mention}.",
                color=discord.Color.red()
            )

        elif action == "disable":
            cfg["lockdown"] = False
            save_guild_security_config(interaction.guild_id, cfg)

            for channel in interaction.guild.text_channels:
                try:
                    overwrites = channel.overwrites_for(interaction.guild.default_role)
                    if overwrites.send_messages is False:
                        overwrites.send_messages = None
                        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites, reason="Security Lockdown Disengaged")
                except Exception:
                    pass

            await interaction.response.send_message("🟢 **LOCKDOWN DISENGAGED.** Channel permissions restored.", ephemeral=True)
            await log_security_event(
                interaction.guild,
                title="LOCKDOWN DISENGAGED",
                description=f"Manual security lockdown was disengaged by {interaction.user.mention}.",
                color=discord.Color.green()
            )

        elif action == "status":
            status = "🔒 LOCKDOWN ACTIVE" if cfg.get("lockdown") else "🔓 Normal Mode"
            await interaction.response.send_message(f"Current Lockdown State: **{status}**", ephemeral=True)

    # 8. RESET CONFIGURATION
    @security_group.command(name="reset", description="Reset security configurations to default values.")
    @app_commands.checks.has_permissions(administrator=True)
    async def security_reset(self, interaction: discord.Interaction):
        save_guild_security_config(interaction.guild_id, DEFAULT_GUILD_SECURITY)
        await interaction.response.send_message("🔄 Security configuration reset to default parameters.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SecurityConfigCog(bot))

