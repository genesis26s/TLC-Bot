import sqlite3
import json
import os
from typing import Optional, Dict, Any, List, Tuple
import discord
from discord.ext import commands, tasks
from discord import app_commands

class TransferDatabase:
    """Handles persistent SQLite database storage for server settings, team rosters, and channel binds."""
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        else:
            env_path = os.getenv("TLC_DB_PATH")
            if env_path:
                self.db_path = env_path
            else:
                cog_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.abspath(os.path.join(cog_dir, ".."))
                
                root_db = os.path.join(project_root, "tlc_transfer_system.db")
                cog_db = os.path.join(cog_dir, "tlc_transfer_system.db")
                
                if os.path.exists(root_db):
                    self.db_path = root_db
                elif os.path.exists(cog_db):
                    self.db_path = cog_db
                else:
                    self.db_path = root_db
            
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Establishes connection with busy timeouts and WAL mode enabled for concurrency."""
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    league_name TEXT DEFAULT '[TLC] Touchline Competitive',
                    transfer_window_open INTEGER DEFAULT 1,
                    signings_enabled INTEGER DEFAULT 1,
                    signing_mode TEXT DEFAULT 'OFFER',
                    roster_cap INTEGER DEFAULT 16,
                    dashboard_url TEXT DEFAULT 'https://genesis26s-tlc-bot-website-xi.vercel.app/'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_roles (
                    guild_id TEXT,
                    role_key TEXT,
                    role_id TEXT,
                    PRIMARY KEY (guild_id, role_key)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_channels (
                    guild_id TEXT,
                    channel_key TEXT,
                    channel_id TEXT,
                    PRIMARY KEY (guild_id, channel_key)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    guild_id TEXT,
                    role_id TEXT,
                    name TEXT,
                    acronym TEXT,
                    emoji TEXT,
                    logo_url TEXT,
                    owner_id TEXT,
                    is_active INTEGER DEFAULT 1,
                    PRIMARY KEY (guild_id, role_id)
                )
            """)
            conn.commit()

    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
            row = cursor.fetchone()
            if not row:
                cursor.execute("INSERT INTO guild_settings (guild_id) VALUES (?)", (str(guild_id),))
                conn.commit()
                cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
                row = cursor.fetchone()
            return dict(row)

    def update_setting(self, guild_id: int, key: str, value: Any) -> None:
        allowed_keys = {"league_name", "transfer_window_open", "signings_enabled", "signing_mode", "roster_cap", "dashboard_url"}
        if key not in allowed_keys:
            raise ValueError(f"Invalid setting key: {key}")
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE guild_settings SET {key} = ? WHERE guild_id = ?", (value, str(guild_id)))
            conn.commit()

    def set_role_id(self, guild_id: int, role_key: str, role_id: Optional[int]) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if role_id is None:
                cursor.execute("DELETE FROM guild_roles WHERE guild_id = ? AND role_key = ?", (str(guild_id), role_key))
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO guild_roles (guild_id, role_key, role_id) VALUES (?, ?, ?)",
                    (str(guild_id), role_key, str(role_id))
                )
            conn.commit()

    def get_role_id(self, guild_id: int, role_key: str) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role_id FROM guild_roles WHERE guild_id = ? AND role_key = ?", (str(guild_id), role_key))
            row = cursor.fetchone()
            return int(row["role_id"]) if row and row["role_id"] else None

    def set_channel_id(self, guild_id: int, channel_key: str, channel_id: Optional[int]) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if channel_id is None:
                cursor.execute("DELETE FROM guild_channels WHERE guild_id = ? AND channel_key = ?", (str(guild_id), channel_key))
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO guild_channels (guild_id, channel_key, channel_id) VALUES (?, ?, ?)",
                    (str(guild_id), channel_key, str(channel_id))
                )
            conn.commit()

    def get_channel_id(self, guild_id: int, channel_key: str) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT channel_id FROM guild_channels WHERE guild_id = ? AND channel_key = ?", (str(guild_id), channel_key))
            row = cursor.fetchone()
            return int(row["channel_id"]) if row and row["channel_id"] else None

    def register_team(self, guild_id: int, role_id: int, name: str, acronym: str, emoji: str = "", logo_url: str = "", owner_id: Optional[int] = None) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO teams (guild_id, role_id, name, acronym, emoji, logo_url, owner_id, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (str(guild_id), str(role_id), name, acronym, emoji, logo_url, str(owner_id) if owner_id else None)
            )
            conn.commit()

    def disband_team(self, guild_id: int, role_id: int) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE teams SET is_active = 0 WHERE guild_id = ? AND role_id = ?", (str(guild_id), str(role_id)))
            conn.commit()

    def get_team_by_role(self, guild_id: int, role_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM teams WHERE guild_id = ? AND role_id = ?", (str(guild_id), str(role_id)))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_teams(self, guild_id: int, only_active: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if only_active:
                cursor.execute("SELECT * FROM teams WHERE guild_id = ? AND is_active = 1", (str(guild_id),))
            else:
                cursor.execute("SELECT * FROM teams WHERE guild_id = ?", (str(guild_id),))
            return [dict(row) for row in cursor.fetchall()]

class TLCTransferCog(commands.Cog):
    """Main discord.py extension handling transfers, team rosters, signing offers, and settings panels."""

    def __init__(self, bot: commands.Bot, db_path: Optional[str] = None):
        self.bot = bot
        self.db = TransferDatabase(db_path=db_path)
        # Store live teamslist messages to automatically auto-update
        self.tracked_teamlists: Dict[int, Tuple[int, int]] = {} # guild_id -> (channel_id, message_id)
        self.update_teamslist_task.start()

    def cog_unload(self):
        self.update_teamslist_task.cancel()

    @tasks.loop(seconds=60)
    async def update_teamslist_task(self):
        """Periodically refreshes all posted live team list embeds."""
        for guild_id, (channel_id, message_id) in list(self.tracked_teamlists.items()):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel = guild.get_channel(channel_id)
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(message_id)
                view = self.TeamsListView(self, guild_id)
                embed = await view.build_embed(guild)
                await msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @update_teamslist_task.before_loop
    async def before_update_teamslist(self):
        await self.bot.wait_until_ready()

    def is_staff(self, member: discord.Member, guild_id: int) -> bool:
        """Checks if a member holds server administrator permissions, is guild owner, or holds configured staff role."""
        if member.id == member.guild.owner_id or member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_roles:
            return True
        staff_role_id = self.db.get_role_id(guild_id, "staff")
        if staff_role_id and any(r.id == staff_role_id for r in member.roles):
            return True
        return False

    def check_manager_permission(self, member: discord.Member, guild_id: int) -> bool:
        """Checks if a member holds any configured management roles or Administrator/Staff permissions."""
        if self.is_staff(member, guild_id):
            return True
        
        mgr_keys = ["owner", "gm", "head_coach", "assistant_coach"]
        manager_role_ids = [self.db.get_role_id(guild_id, k) for k in mgr_keys]
        valid_mgr_ids = [rid for rid in manager_role_ids if rid is not None]

        user_role_ids = [r.id for r in member.roles]
        return any(rid in user_role_ids for rid in valid_mgr_ids)

    def is_strict_manager(self, member: discord.Member, guild_id: int) -> bool:
        """Strict check to see if user holds General Manager / Manager role."""
        gm_role_id = self.db.get_role_id(guild_id, "gm")
        owner_role_id = self.db.get_role_id(guild_id, "owner")
        user_role_ids = [r.id for r in member.roles]
        return (gm_role_id in user_role_ids) if gm_role_id else False or ((owner_role_id in user_role_ids) if owner_role_id else False)

    def check_signing_permission(self, member: discord.Member, guild_id: int) -> bool:
        """Checks if a member holds signing/release authority: Owner, GM (Manager), Head Coach (Co-Manager), or Staff.
        Note: Assistant Manager / Captain is explicitly excluded from signing and releasing."""
        if self.is_staff(member, guild_id):
            return True
        
        signing_mgr_keys = ["owner", "gm", "head_coach"]
        manager_role_ids = [self.db.get_role_id(guild_id, k) for k in signing_mgr_keys]
        valid_mgr_ids = [rid for rid in manager_role_ids if rid is not None]

        user_role_ids = [r.id for r in member.roles]
        return any(rid in user_role_ids for rid in valid_mgr_ids)

    def auto_detect_user_team(self, member: discord.Member, guild_id: int) -> Tuple[Optional[discord.Role], Optional[Dict[str, Any]]]:
        """Auto-detects a manager's assigned registered team from their assigned Discord roles."""
        all_teams = self.db.get_all_teams(guild_id, only_active=True)
        if not all_teams:
            return None, None

        registered_role_ids = {int(t["role_id"]): t for t in all_teams}
        
        for role in member.roles:
            if role.id in registered_role_ids:
                return role, registered_role_ids[role.id]

        return None, None

    def get_player_current_team(self, member: discord.Member, guild_id: int) -> Optional[Dict[str, Any]]:
        """Checks if a member is currently assigned to any active registered franchise team role."""
        all_teams = self.db.get_all_teams(guild_id, only_active=True)
        if not all_teams:
            return None

        registered_role_ids = {int(t["role_id"]): t for t in all_teams}
        for role in member.roles:
            if role.id in registered_role_ids:
                return registered_role_ids[role.id]

        return None

    def get_team_manager(self, guild: discord.Guild, team_role: Optional[discord.Role], team_info: Optional[Dict[str, Any]] = None) -> Optional[discord.Member]:
        """Finds team manager strictly by checking members with team role using role hierarchy:
        Franchise Owner > General Manager (Manager) / Head Coach (Co Manager) > Assistant Manager (Captain)."""
        if not team_role:
            return None

        owner_role_id = self.db.get_role_id(guild.id, "owner")
        gm_role_id = self.db.get_role_id(guild.id, "gm")
        hc_role_id = self.db.get_role_id(guild.id, "head_coach")
        ac_role_id = self.db.get_role_id(guild.id, "assistant_coach")

        team_members = [m for m in guild.members if team_role in m.roles]
        if not team_members:
            return None

        # Priority 1: Franchise Owner
        if owner_role_id:
            for member in team_members:
                if any(r.id == owner_role_id for r in member.roles):
                    return member

        # Priority 2: General Manager (Manager) or Head Coach (Co Manager)
        for member in team_members:
            user_rids = {r.id for r in member.roles}
            if (gm_role_id and gm_role_id in user_rids) or (hc_role_id and hc_role_id in user_rids):
                return member

        # Priority 3: Assistant Manager / Captain
        if ac_role_id:
            for member in team_members:
                if any(r.id == ac_role_id for r in member.roles):
                    return member

        return None

    class TeamsListView(discord.ui.View):
        """Interactive View for /teamlist featuring live refresh functionality and organized layout."""
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=None) # Persistent view
            self.cog = cog
            self.guild_id = guild_id

        async def build_embed(self, guild: discord.Guild) -> discord.Embed:
            all_teams = self.cog.db.get_all_teams(guild.id, only_active=False)
            settings = self.cog.db.get_guild_settings(guild.id)
            roster_cap = settings["roster_cap"]

            if not all_teams:
                embed = discord.Embed(
                    title="🏆 Franchise Teams Directory",
                    description="*No franchise teams are currently registered in this server.*",
                    color=discord.Color.from_rgb(220, 38, 38)
                )
                embed.set_author(name=settings["league_name"])
                return embed

            active_teams = []
            inactive_teams = []
            total_signed_players = 0

            for t in all_teams:
                role = guild.get_role(int(t["role_id"]))
                signed_count = sum(1 for m in guild.members if role in m.roles) if role else 0
                total_signed_players += signed_count
                manager_member = self.cog.get_team_manager(guild, role, t) if role else None

                is_active_db = t.get("is_active", 1)
                if not is_active_db or (manager_member is None and signed_count == 0):
                    inactive_teams.append((t, role, signed_count, manager_member))
                else:
                    active_teams.append((t, role, signed_count, manager_member))

            timestamp_now = int(discord.utils.utcnow().timestamp())
            timestamp_str = f"<t:{timestamp_now}:R>"

            embed = discord.Embed(
                title="🏆 Franchise Teams Directory",
                description=(
                    f"**League:** {settings['league_name']}\n"
                    f"**Total Teams:** `{len(all_teams)}` | **Active:** `{len(active_teams)}` | **Inactive:** `{len(inactive_teams)}` | **Total Players:** `{total_signed_players}`\n"
                    f"*Last Refreshed: {timestamp_str} (Auto-Updates Automatically)*"
                ),
                color=discord.Color.from_rgb(220, 38, 38)
            )
            embed.set_author(name=settings["league_name"])

            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)

            # Active Teams Section
            if active_teams:
                active_lines = []
                for t, role, count, mgr in active_teams:
                    emoji = t.get("emoji", "").strip()
                    emoji_str = f"{emoji} " if emoji else ""
                    role_str = role.mention if role else "`Role Removed`"
                    mgr_str = mgr.mention if mgr else "*No Manager Assigned*"
                    
                    if count >= roster_cap:
                        cap_badge = f"🔴 **{count}/{roster_cap}** (FULL)"
                    else:
                        cap_badge = f"🟢 **{count}/{roster_cap}**"

                    line = (
                        f"{emoji_str}**{t.get('name', 'Team')}** (`{t.get('acronym', 'TEAM')}`)\n"
                        f"└ **Role:** {role_str} • **Manager:** {mgr_str}\n"
                        f"└ **Roster:** {cap_badge}"
                    )
                    active_lines.append(line)

                chunk_text = ""
                for line in active_lines:
                    if len(chunk_text) + len(line) + 2 > 1000:
                        embed.add_field(name="🟢 Active Franchises", value=chunk_text, inline=False)
                        chunk_text = line + "\n\n"
                    else:
                        chunk_text += line + "\n\n"
                if chunk_text:
                    embed.add_field(name="🟢 Active Franchises", value=chunk_text.strip(), inline=False)

            # Inactive Teams Section
            if inactive_teams:
                inactive_lines = []
                for t, role, count, mgr in inactive_teams:
                    emoji = t.get("emoji", "").strip()
                    emoji_str = f"{emoji} " if emoji else ""
                    role_str = role.mention if role else "`Role Removed`"
                    mgr_str = mgr.mention if mgr else "*Unassigned*"

                    line = (
                        f"{emoji_str}**{t.get('name', 'Team')}** (`{t.get('acronym', 'TEAM')}`)\n"
                        f"└ **Role:** {role_str} • **Manager:** {mgr_str} • **Players:** `{count}/{roster_cap}`"
                    )
                    inactive_lines.append(line)

                chunk_text = ""
                for line in inactive_lines:
                    if len(chunk_text) + len(line) + 2 > 1000:
                        embed.add_field(name="🔴 Inactive / Vacant Franchises", value=chunk_text, inline=False)
                        chunk_text = line + "\n\n"
                    else:
                        chunk_text += line + "\n\n"
                if chunk_text:
                    embed.add_field(name="🔴 Inactive / Vacant Franchises", value=chunk_text.strip(), inline=False)

            embed.set_footer(text="This team list auto-updates periodically.")
            return embed

        @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="btn_refresh_teams")
        async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            embed = await self.build_embed(interaction.guild)
            await interaction.response.edit_message(embed=embed, view=self)

    class MainPanelNavView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

            settings = self.cog.db.get_guild_settings(guild_id)
            url = settings.get("dashboard_url", "https://genesis26s-tlc-bot-website-xi.vercel.app/")
            self.add_item(discord.ui.Button(label="Dashboard", url=url, style=discord.ButtonStyle.link))

        @discord.ui.button(label="Roles", style=discord.ButtonStyle.secondary, custom_id="btn_panel_roles")
        async def roles_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.cog.is_staff(interaction.user, interaction.guild_id):
                await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="Roles Configuration",
                description="Choose a category for your specific permission or role setup.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            embed.add_field(
                name="Categories",
                value="• **Franchise Roles** - Assign authority roles for team management\n"
                      "• **Server Roles** - Set staff and server badge roles\n"
                      "• **Create Roles** - Create new team roles",
                inline=False
            )
            view = TLCTransferCog.RolesSubPanelView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

        @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, custom_id="btn_panel_channels")
        async def channels_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.cog.is_staff(interaction.user, interaction.guild_id):
                await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
                return

            embed = discord.Embed(
                title="Server Channels",
                description="Choose which channel to bind for system actions.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            embed.add_field(
                name="Bound Channels",
                value="• **Transactions** - Transfer, signing, and release announcements\n"
                      "• **Demands** - Player transfer/release demand submissions\n"
                      "• **LFP** - Looking for players recruitment posts\n"
                      "• **Gametime** - Match schedule alerts and lobby codes\n"
                      "• **Alerts** - Administrative logs and disband alerts\n"
                      "• **Stat Dump** - Dedicated channel for match statistics",
                inline=False
            )
            view = TLCTransferCog.ChannelsSubPanelView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

        @discord.ui.button(label="Signings", style=discord.ButtonStyle.secondary, custom_id="btn_panel_signings")
        async def signings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.cog.is_staff(interaction.user, interaction.guild_id):
                await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
                return

            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title="Server Signings",
                description="Choose which signing option to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            status_text = "ENABLED" if settings["signings_enabled"] else "DISABLED"
            embed.add_field(
                name="Current Settings",
                value=f"• **Franchise Signings:** `{status_text}`\n"
                      f"• **Signing Choice:** `{settings['signing_mode']}`\n"
                      f"• **Roster Cap:** `{settings['roster_cap']}` players",
                inline=False
            )
            view = TLCTransferCog.SigningsSubPanelView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class RolesSubPanelView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

        @discord.ui.button(label="Franchise Roles", style=discord.ButtonStyle.secondary)
        async def franchise_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
            embed = discord.Embed(
                title="Franchise Roles",
                description="Configure specific authority roles for team management.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            embed.add_field(
                name="Hierarchy Levels",
                value="• **Franchise Owner** - Complete control over franchise\n"
                      "• **General Manager (Manager)** - Can sign & release players\n"
                      "• **Head Coach (Co Manager)** - Can sign & release players\n"
                      "• **Assistant Manager (Captain)** - Team leader (cannot sign & release players)",
                inline=False
            )
            view = TLCTransferCog.FranchiseRolesView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

        @discord.ui.button(label="Server Roles", style=discord.ButtonStyle.secondary)
        async def server_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
            embed = discord.Embed(
                title="Server Roles",
                description="Select roles such as Staff, Free Agent, Streamer, or Referee.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.ServerRolesView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

        @discord.ui.button(label="Main Page", style=discord.ButtonStyle.primary)
        async def main_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title=f"{settings['league_name']} Settings Panel",
                description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.MainPanelNavView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class FranchiseRolesView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

        async def _set_role_prompt(self, interaction: discord.Interaction, role_key: str, role_title: str):
            select = discord.ui.RoleSelect(placeholder=f"Select {role_title} Role...", min_values=1, max_values=1)
            
            async def select_callback(select_interaction: discord.Interaction):
                selected_role = select.values[0]
                self.cog.db.set_role_id(self.guild_id, role_key, selected_role.id)
                await select_interaction.response.send_message(f"Successfully bound **{role_title}** to {selected_role.mention}.", ephemeral=True)

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message(f"Choose the role for **{role_title}**:", view=view, ephemeral=True)

        @discord.ui.button(label="Franchise Owner", style=discord.ButtonStyle.secondary)
        async def set_owner(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "owner", "Franchise Owner")

        @discord.ui.button(label="General Manager (Manager)", style=discord.ButtonStyle.secondary)
        async def set_gm(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "gm", "General Manager (Manager)")

        @discord.ui.button(label="Head Coach (Co Manager)", style=discord.ButtonStyle.secondary)
        async def set_hc(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "head_coach", "Head Coach (Co Manager)")

        @discord.ui.button(label="Assistant Manager (Captain)", style=discord.ButtonStyle.secondary)
        async def set_ac(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "assistant_coach", "Assistant Manager (Captain)")

        @discord.ui.button(label="Main Page", style=discord.ButtonStyle.primary)
        async def main_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title=f"{settings['league_name']} Settings Panel",
                description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.MainPanelNavView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class ServerRolesView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

        async def _set_role_prompt(self, interaction: discord.Interaction, role_key: str, role_title: str):
            select = discord.ui.RoleSelect(placeholder=f"Select {role_title} Role...", min_values=1, max_values=1)
            
            async def select_callback(select_interaction: discord.Interaction):
                selected_role = select.values[0]
                self.cog.db.set_role_id(self.guild_id, role_key, selected_role.id)
                await select_interaction.response.send_message(f"Successfully bound **{role_title}** to {selected_role.mention}.", ephemeral=True)

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message(f"Choose the role for **{role_title}**:", view=view, ephemeral=True)

        @discord.ui.button(label="Staff Role", style=discord.ButtonStyle.secondary)
        async def set_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "staff", "Staff")

        @discord.ui.button(label="Free Agent Role", style=discord.ButtonStyle.secondary)
        async def set_fa(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._set_role_prompt(interaction, "free_agent", "Free Agent")

        @discord.ui.button(label="Main Page", style=discord.ButtonStyle.primary)
        async def main_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title=f"{settings['league_name']} Settings Panel",
                description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.MainPanelNavView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class ChannelsSubPanelView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

        async def _prompt_channel_select(self, interaction: discord.Interaction, channel_key: str, channel_title: str):
            select = discord.ui.ChannelSelect(
                placeholder=f"Select {channel_title} Channel...",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1
            )

            async def select_callback(select_interaction: discord.Interaction):
                ch = select.values[0]
                self.cog.db.set_channel_id(self.guild_id, channel_key, ch.id)
                await select_interaction.response.send_message(f"Bound **{channel_title}** to {ch.mention}.", ephemeral=True)

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message(f"Select target channel for **{channel_title}**:", view=view, ephemeral=True)

        @discord.ui.button(label="Transactions", style=discord.ButtonStyle.secondary)
        async def bind_tx(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "transactions", "Transactions")

        @discord.ui.button(label="Demands", style=discord.ButtonStyle.secondary)
        async def bind_demands(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "demands", "Demands")

        @discord.ui.button(label="LFP", style=discord.ButtonStyle.secondary)
        async def bind_lfp(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "lfp", "LFP")

        @discord.ui.button(label="Gametime", style=discord.ButtonStyle.secondary)
        async def bind_gametime(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "gametime", "Gametime")

        @discord.ui.button(label="Alerts", style=discord.ButtonStyle.secondary)
        async def bind_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "alerts", "Alerts")

        @discord.ui.button(label="Stat Dump", style=discord.ButtonStyle.secondary)
        async def bind_stat_dump(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._prompt_channel_select(interaction, "stat_dump", "Stat Dump")

        @discord.ui.button(label="Main Page", style=discord.ButtonStyle.primary)
        async def main_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title=f"{settings['league_name']} Settings Panel",
                description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.MainPanelNavView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class SigningsSubPanelView(discord.ui.View):
        def __init__(self, cog: 'TLCTransferCog', guild_id: int):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild_id = guild_id

        @discord.ui.button(label="Franchise Signings", style=discord.ButtonStyle.secondary)
        async def toggle_signings(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            new_val = 0 if settings["signings_enabled"] else 1
            self.cog.db.update_setting(interaction.guild_id, "signings_enabled", new_val)
            status_text = "ENABLED" if new_val else "DISABLED"
            await interaction.response.send_message(f"Franchise Signings are now **{status_text}**.", ephemeral=True)

        @discord.ui.button(label="Signing Choice", style=discord.ButtonStyle.secondary)
        async def toggle_choice(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            new_mode = "DIRECT" if settings["signing_mode"] == "OFFER" else "OFFER"
            self.cog.db.update_setting(interaction.guild_id, "signing_mode", new_mode)
            await interaction.response.send_message(f"Default signing mode switched to **{new_mode}**.", ephemeral=True)

        @discord.ui.button(label="Roster Cap", style=discord.ButtonStyle.secondary)
        async def set_roster_cap(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = discord.ui.Modal(title="Set Roster Cap")
            cap_input = discord.ui.TextInput(label="Roster Cap Amount", placeholder="16", default="16", min_length=1, max_length=3)
            modal.add_item(cap_input)

            async def modal_callback(modal_interaction: discord.Interaction):
                try:
                    cap_val = int(cap_input.value)
                    self.cog.db.update_setting(interaction.guild_id, "roster_cap", cap_val)
                    await modal_interaction.response.send_message(f"Roster Cap successfully updated to **{cap_val}** players.", ephemeral=True)
                except ValueError:
                    await modal_interaction.response.send_message("Invalid number provided.", ephemeral=True)

            modal.on_submit = modal_callback
            await interaction.response.send_modal(modal)

        @discord.ui.button(label="Main Page", style=discord.ButtonStyle.primary)
        async def main_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            settings = self.cog.db.get_guild_settings(interaction.guild_id)
            embed = discord.Embed(
                title=f"{settings['league_name']} Settings Panel",
                description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            view = TLCTransferCog.MainPanelNavView(self.cog, interaction.guild_id)
            await interaction.response.edit_message(embed=embed, view=view)

    class ContractInteractiveView(discord.ui.View):
        def __init__(self, target_player: discord.Member, manager: discord.Member, team_role: discord.Role, cog: 'TLCTransferCog'):
            super().__init__(timeout=86400) # 24 hours
            self.target_player = target_player
            self.manager = manager
            self.team_role = team_role
            self.cog = cog

        @discord.ui.button(label="Accept Offer", style=discord.ButtonStyle.success, custom_id="btn_accept_contract")
        async def accept_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.target_player.id:
                await interaction.response.send_message("This contract proposal is not addressed to you.", ephemeral=True)
                return

            guild = self.manager.guild
            member = guild.get_member(self.target_player.id)
            if not member:
                await interaction.response.send_message("Member no longer in server.", ephemeral=True)
                return

            settings = self.cog.db.get_guild_settings(guild.id)

            if not settings["transfer_window_open"]:
                await interaction.response.send_message("Signing failed: The Transfer Window is currently **CLOSED**.", ephemeral=True)
                return

            if not settings["signings_enabled"]:
                await interaction.response.send_message("Signing failed: Signings are currently **DISABLED**.", ephemeral=True)
                return

            existing_team = self.cog.get_player_current_team(member, guild.id)
            if existing_team:
                await interaction.response.send_message(
                    f"Signing failed: You are currently signed to **{existing_team.get('name', 'another team')}**. You must submit a demand or be released first.",
                    ephemeral=True
                )
                return

            roster_cap = settings["roster_cap"]
            current_roster = sum(1 for m in guild.members if self.team_role in m.roles)

            if current_roster >= roster_cap:
                await interaction.response.send_message(
                    f"Signing failed! **{self.team_role.name}** is at maximum roster capacity ({current_roster}/{roster_cap}).",
                    ephemeral=True
                )
                return

            if self.team_role in member.roles:
                await interaction.response.send_message(f"You are already signed to **{self.team_role.name}**.", ephemeral=True)
                return

            try:
                await member.add_roles(self.team_role, reason="Accepted contract signing offer")
                
                fa_role_id = self.cog.db.get_role_id(guild.id, "free_agent")
                if fa_role_id:
                    fa_role = guild.get_role(fa_role_id)
                    if fa_role and fa_role in member.roles:
                        await member.remove_roles(fa_role, reason="Signed to team roster")

            except discord.Forbidden:
                await interaction.response.send_message("Failed to update roles due to missing bot permissions.", ephemeral=True)
                return

            team_info = self.cog.db.get_team_by_role(guild.id, self.team_role.id)
            emoji_str = f"{team_info.get('emoji', '')} " if team_info and team_info.get("emoji") else ""

            try:
                mgr_dm_embed = discord.Embed(
                    title="Success!",
                    description=f"{member.mention} (`{member.name}`) has `accepted` your offer in `{settings['league_name']}` {emoji_str}**{self.team_role.name}**.",
                    color=discord.Color.from_rgb(34, 197, 94)
                )
                mgr_dm_embed.set_author(name="TeamSign")
                mgr_dm_embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/5610/5610944.png")
                await self.manager.send(embed=mgr_dm_embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

            tx_channel_id = self.cog.db.get_channel_id(guild.id, "transactions")
            if tx_channel_id:
                tx_channel = guild.get_channel(tx_channel_id)
                if tx_channel:
                    embed = discord.Embed(
                        title="Official Signing Announcement",
                        description=f"**{member.mention}** (`{member.display_name}`) has officially signed with {emoji_str}**{self.team_role.mention}**!",
                        color=discord.Color.from_rgb(220, 38, 38)
                    )
                    embed.add_field(name="Signed By", value=self.manager.mention, inline=True)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await tx_channel.send(embed=embed)

            for b in self.children:
                b.disabled = True
            await interaction.response.edit_message(content=f"You have **ACCEPTED** the contract offer from **{self.team_role.name}**!", view=self)

        @discord.ui.button(label="Decline Offer", style=discord.ButtonStyle.danger, custom_id="btn_decline_contract")
        async def decline_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.target_player.id:
                await interaction.response.send_message("This contract proposal is not addressed to you.", ephemeral=True)
                return

            for b in self.children:
                b.disabled = True
            await interaction.response.edit_message(content=f"You have **DECLINED** the contract offer from **{self.team_role.name}**.", view=self)

    @app_commands.command(name="appoint", description="Appoint a member to a club roster (Staff / Managers).")
    @app_commands.describe(
        player="The user to appoint to the club",
        team="Optional team role (auto-detected from manager if omitted)"
    )
    async def appoint_command(self, interaction: discord.Interaction, player: discord.Member, team: Optional[discord.Role] = None):
        """Appoints a player to a club roster directly."""
        guild = interaction.guild
        if not self.check_signing_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Only Staff or Managers can appoint players.", ephemeral=True)
            return

        target_team = team
        if not target_team:
            detected_role, _ = self.auto_detect_user_team(interaction.user, guild.id)
            if not detected_role:
                await interaction.response.send_message("Could not auto-detect your team. Please specify the `team:` parameter.", ephemeral=True)
                return
            target_team = detected_role

        existing_team = self.get_player_current_team(player, guild.id)
        if existing_team:
            await interaction.response.send_message(
                f"Appointment failed: **{player.display_name}** is already signed to **{existing_team.get('name', 'another team')}**.",
                ephemeral=True
            )
            return

        settings = self.db.get_guild_settings(guild.id)
        roster_cap = settings["roster_cap"]
        current_roster = sum(1 for m in guild.members if target_team in m.roles)

        if current_roster >= roster_cap:
            await interaction.response.send_message(f"Roster limit reached! **{target_team.name}** is full ({current_roster}/{roster_cap}).", ephemeral=True)
            return

        try:
            await player.add_roles(target_team, reason=f"Appointed by {interaction.user.display_name}")
            fa_role_id = self.db.get_role_id(guild.id, "free_agent")
            if fa_role_id:
                fa_role = guild.get_role(fa_role_id)
                if fa_role and fa_role in player.roles:
                    await player.remove_roles(fa_role, reason="Appointed to club roster")
        except discord.Forbidden:
            await interaction.response.send_message("Failed to update roles due to missing bot permissions.", ephemeral=True)
            return

        team_info = self.db.get_team_by_role(guild.id, target_team.id)
        emoji_str = f"{team_info.get('emoji', '')} " if team_info and team_info.get("emoji") else ""

        embed = discord.Embed(
            title="Club Appointment Announcement",
            description=f"**{player.mention}** (`{player.display_name}`) has been officially appointed to {emoji_str}**{target_team.mention}**!",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.add_field(name="Appointed By", value=interaction.user.mention, inline=True)
        embed.set_thumbnail(url=player.display_avatar.url)

        await interaction.response.send_message(embed=embed)

        tx_channel_id = self.db.get_channel_id(guild.id, "transactions")
        if tx_channel_id:
            tx_channel = guild.get_channel(tx_channel_id)
            if tx_channel:
                await tx_channel.send(embed=embed)

    @app_commands.command(name="membercount", description="View server member statistics and team roster distribution.")
    async def membercount_command(self, interaction: discord.Interaction):
        """Displays total member metrics, including signed team players and free agents."""
        guild = interaction.guild
        total_members = guild.member_count or len(guild.members)
        humans = sum(1 for m in guild.members if not m.bot)
        bots = sum(1 for m in guild.members if m.bot)

        all_teams = self.db.get_all_teams(guild.id, only_active=True)
        registered_team_ids = {int(t["role_id"]) for t in all_teams}

        signed_players_count = sum(
            1 for m in guild.members if any(r.id in registered_team_ids for r in m.roles)
        )

        fa_role_id = self.db.get_role_id(guild.id, "free_agent")
        fa_count = 0
        if fa_role_id:
            fa_role = guild.get_role(fa_role_id)
            if fa_role:
                fa_count = sum(1 for m in guild.members if fa_role in m.roles)

        embed = discord.Embed(
            title=f"📊 {guild.name} Member Breakdown",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.add_field(name="Total Members", value=f"`{total_members}`", inline=True)
        embed.add_field(name="Humans", value=f"`{humans}`", inline=True)
        embed.add_field(name="Bots", value=f"`{bots}`", inline=True)
        embed.add_field(name="Signed Players", value=f"`{signed_players_count}`", inline=True)
        embed.add_field(name="Free Agents", value=f"`{fa_count}`", inline=True)

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="panel", description="Opens interactive transfer settings and configuration panel (Staff Only).")
    async def panel_command(self, interaction: discord.Interaction):
        """Displays interactive setup panel with navigation buttons."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Administrator or Staff role required.", ephemeral=True)
            return

        settings = self.db.get_guild_settings(interaction.guild_id)
        embed = discord.Embed(
            title=f"{settings['league_name']} Settings Panel",
            description=f"Welcome to Panel, choose an attribute to set, or visit {settings['dashboard_url']} to configure.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.add_field(
            name="Configuration Options",
            value="• **Roles** - Set server & management roles\n"
                  "• **Channels** - Set system channels for transfers & alerts\n"
                  "• **Signings** - Configure signing options, mode, and roster caps",
            inline=False
        )

        view = self.MainPanelNavView(self, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="setchannel", description="Directly bind system channels for transactions, demands, or alerts (Staff Only).")
    @app_commands.describe(
        key="The system function channel key",
        channel="The target text/news channel to bind"
    )
    @app_commands.choices(key=[
        app_commands.Choice(name="Transactions", value="transactions"),
        app_commands.Choice(name="Demands", value="demands"),
        app_commands.Choice(name="LFP (Looking For Players)", value="lfp"),
        app_commands.Choice(name="Gametime", value="gametime"),
        app_commands.Choice(name="Alerts", value="alerts"),
        app_commands.Choice(name="Stat Dump", value="stat_dump")
    ])
    async def setchannel_command(self, interaction: discord.Interaction, key: app_commands.Choice[str], channel: discord.TextChannel):
        """Allows direct channel binding bypassing Discord UI limits."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
            return

        self.db.set_channel_id(interaction.guild_id, key.value, channel.id)
        await interaction.response.send_message(f"Successfully bound **{key.name}** channel to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="addteam", description="Register an existing server role as a franchise team (Staff Only).")
    @app_commands.describe(role="The team role to register", emoji="Custom server emoji tag or unicode emoji for the team")
    async def addteam_command(self, interaction: discord.Interaction, role: discord.Role, emoji: str):
        """Registers an existing server role as a franchise team without setting staff as team owner."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Staff or Administrator role required.", ephemeral=True)
            return

        acronym = role.name[:3].upper()
        self.db.register_team(
            guild_id=interaction.guild_id,
            role_id=role.id,
            name=role.name,
            acronym=acronym,
            emoji=emoji.strip(),
            owner_id=None
        )

        embed = discord.Embed(
            title="Franchise Team Registered",
            description=f"Successfully registered **{role.name}** ({role.mention}) as an active franchise team.\n\n"
                        f"• **Emoji:** {emoji.strip()}\n"
                        f"• **Acronym:** `{acronym}`",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="viewteam", description="View team info, status, and current signed roster.")
    @app_commands.describe(team="The team role to inspect")
    async def viewteam_command(self, interaction: discord.Interaction, team: discord.Role):
        """Displays team details and lists all members holding the team role."""
        guild = interaction.guild
        team_info = self.db.get_team_by_role(guild.id, team.id)
        settings = self.db.get_guild_settings(guild.id)
        roster_cap = settings["roster_cap"]

        signed_members = [m for m in guild.members if team in m.roles]
        manager_member = self.get_team_manager(guild, team, team_info)

        emoji_str = f"{team_info.get('emoji', '')} " if team_info and team_info.get("emoji") else ""
        
        is_active_db = team_info.get("is_active", 1) if team_info else 1
        if not is_active_db or (manager_member is None and len(signed_members) == 0):
            status_str = "Unactive"
        else:
            status_str = "Active"

        owner_str = manager_member.mention if manager_member else "None"

        embed = discord.Embed(
            title=f"{emoji_str}{team.name}",
            description=f"**Status:** `{status_str}`\n"
                        f"**Roster Count:** {len(signed_members)} / {roster_cap}",
            color=team.color if team.color.value != 0 else discord.Color.from_rgb(220, 38, 38)
        )
        embed.set_author(name=settings["league_name"])

        embed.add_field(name="Manager / Owner", value=owner_str, inline=True)

        if team_info and team_info.get("logo_url"):
            embed.set_thumbnail(url=team_info["logo_url"])
        elif guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        if signed_members:
            player_list = [f"• {m.mention} (`{m.display_name}`)" for m in signed_members]
            roster_text = "\n".join(player_list)
            if len(roster_text) > 1024:
                roster_text = roster_text[:1020] + "..."
            embed.add_field(name=f"Signed Players ({len(signed_members)})", value=roster_text, inline=False)
        else:
            embed.add_field(name="Signed Players", value="No players currently signed to this roster.", inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="teamslist", description="Displays an organized embed list of all registered teams with live refresh.")
    async def teamslist_command(self, interaction: discord.Interaction):
        """Displays organized directory of all franchise teams registered in the server database and registers tracking for auto-updates."""
        view = self.TeamsListView(self, interaction.guild_id)
        embed = await view.build_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)
        
        msg = await interaction.original_response()
        self.tracked_teamlists[interaction.guild_id] = (interaction.channel_id, msg.id)

    @app_commands.command(name="teamlist", description="Displays an organized embed list of all registered teams with live refresh.")
    async def teamlist_command(self, interaction: discord.Interaction):
        """Alias for /teamslist to automatically update."""
        await self.teamslist_command(interaction)

    @app_commands.command(name="disband", description="Disband a franchise team and convert signed roster back to Free Agency (Staff Only).")
    @app_commands.describe(team="The team role to disband")
    async def disband_command(self, interaction: discord.Interaction, team: discord.Role):
        """Disbands a team and strips roles from members."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
            return

        guild = interaction.guild
        self.db.disband_team(guild.id, team.id)

        fa_role_id = self.db.get_role_id(guild.id, "free_agent")
        fa_role = guild.get_role(fa_role_id) if fa_role_id else None

        affected_count = 0
        for m in guild.members:
            if team in m.roles:
                affected_count += 1
                try:
                    await m.remove_roles(team, reason="Team Disbanded")
                    if fa_role:
                        await m.add_roles(fa_role, reason="Reassigned to Free Agency on Disband")
                except discord.Forbidden:
                    pass

        embed = discord.Embed(
            title="Franchise Team Disbanded",
            description=f"**{team.name}** has been officially disbanded.\n"
                        f"• Status set to `Unactive`\n"
                        f"• {affected_count} players released back to Free Agency.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.response.send_message(embed=embed)

        alerts_id = self.db.get_channel_id(guild.id, "alerts")
        if alerts_id:
            ch = guild.get_channel(alerts_id)
            if ch:
                await ch.send(embed=embed)

    @app_commands.command(name="swapteams", description="swap two teams players")
    @app_commands.describe(
        team1="First team role to swap",
        team2="Second team role to swap"
    )
    async def swapteams_command(self, interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
        """Swaps the entire player rosters between two specified team roles (Staff Only)."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Staff or Administrator role required.", ephemeral=True)
            return

        if team1.id == team2.id:
            await interaction.response.send_message("Cannot swap a team with itself. Please select two distinct team roles.", ephemeral=True)
            return

        guild = interaction.guild
        members_team1 = [m for m in guild.members if team1 in m.roles]
        members_team2 = [m for m in guild.members if team2 in m.roles]

        if not members_team1 and not members_team2:
            await interaction.response.send_message("Both teams currently have no signed players to swap.", ephemeral=True)
            return

        await interaction.response.defer()

        swapped_t1_count = 0
        swapped_t2_count = 0

        for member in members_team1:
            try:
                await member.remove_roles(team1, reason=f"Swapping rosters between {team1.name} and {team2.name}")
                await member.add_roles(team2, reason=f"Swapping rosters between {team1.name} and {team2.name}")
                swapped_t1_count += 1
            except discord.Forbidden:
                pass

        for member in members_team2:
            try:
                await member.remove_roles(team2, reason=f"Swapping rosters between {team1.name} and {team2.name}")
                await member.add_roles(team1, reason=f"Swapping rosters between {team1.name} and {team2.name}")
                swapped_t2_count += 1
            except discord.Forbidden:
                pass

        embed = discord.Embed(
            title="Team Roster Swap Executed",
            description=f"Successfully swapped active player rosters between **{team1.name}** and **{team2.name}**!\n\n"
                        f"• **{team1.name}** ➔ {swapped_t1_count} player(s) moved to **{team2.name}**\n"
                        f"• **{team2.name}** ➔ {swapped_t2_count} player(s) moved to **{team1.name}**",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.followup.send(embed=embed)

        tx_ch_id = self.db.get_channel_id(guild.id, "transactions")
        if tx_ch_id:
            tx_ch = guild.get_channel(tx_ch_id)
            if tx_ch:
                await tx_ch.send(embed=embed)

    @app_commands.command(name="sign", description="Sign a player directly to your team roster (Managers only).")
    @app_commands.describe(player="The player to sign", team="Optional team role override (auto-detected if omitted)")
    async def sign_command(self, interaction: discord.Interaction, player: discord.Member, team: Optional[discord.Role] = None):
        """Processes direct signing or contract proposal with team auto-detection and sends DM to player."""
        guild = interaction.guild
        settings = self.db.get_guild_settings(guild.id)

        if not settings["transfer_window_open"]:
            await interaction.response.send_message("The Transfer Window is currently **CLOSED**.", ephemeral=True)
            return

        if not settings["signings_enabled"]:
            await interaction.response.send_message("Signings are currently turned **OFF**.", ephemeral=True)
            return

        if not self.check_signing_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Only General Managers (Manager) and Head Coaches (Co-Manager) can sign players.", ephemeral=True)
            return

        target_team = team
        if not target_team:
            detected_role, _ = self.auto_detect_user_team(interaction.user, guild.id)
            if not detected_role:
                await interaction.response.send_message("Could not auto-detect your team. Please specify the `team:` parameter or assign your team role.", ephemeral=True)
                return
            target_team = detected_role

        existing_team = self.get_player_current_team(player, guild.id)
        if existing_team:
            await interaction.response.send_message(
                f"Signing failed: **{player.display_name}** is already signed to **{existing_team.get('name', 'another team')}**. They must submit a demand or be released first.",
                ephemeral=True
            )
            return

        roster_cap = settings["roster_cap"]
        current_roster = sum(1 for m in guild.members if target_team in m.roles)

        if current_roster >= roster_cap:
            await interaction.response.send_message(f"Roster limit reached! **{target_team.name}** is at maximum capacity ({current_roster}/{roster_cap}).", ephemeral=True)
            return

        embed = discord.Embed(
            title="Contract Proposal",
            description=f"{player.mention}, **{interaction.user.mention}** representing **{target_team.name}** has offered you a contract.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.set_author(name=settings["league_name"])

        view = self.ContractInteractiveView(target_player=player, manager=interaction.user, team_role=target_team, cog=self)

        try:
            await player.send(embed=embed, view=view)
            await interaction.response.send_message(f"Contract offer successfully sent to {player.mention} via Direct Message.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(content=f"Could not DM {player.mention} (DMs closed). Sent proposal in channel instead:", embed=embed, view=view)

    @app_commands.command(name="offer", description="Send an offer proposal to a player via Direct Message (Managers only).")
    @app_commands.describe(player="Target player user", team="Optional team role override (auto-detected if omitted)")
    async def offer_command(self, interaction: discord.Interaction, player: discord.Member, team: Optional[discord.Role] = None):
        """Sends contract offer using auto-detected team directly to player's DMs."""
        guild = interaction.guild
        settings = self.db.get_guild_settings(guild.id)

        if not settings["transfer_window_open"]:
            await interaction.response.send_message("Transfer window is currently closed.", ephemeral=True)
            return

        if not settings["signings_enabled"]:
            await interaction.response.send_message("Signings are currently turned **OFF**.", ephemeral=True)
            return

        if not self.check_signing_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Only General Managers (Manager) and Head Coaches (Co-Manager) can offer contracts.", ephemeral=True)
            return

        target_team = team
        if not target_team:
            detected_role, _ = self.auto_detect_user_team(interaction.user, guild.id)
            if not detected_role:
                await interaction.response.send_message("Could not auto-detect your team. Please assign your team role or select `team:` parameter.", ephemeral=True)
                return
            target_team = detected_role

        existing_team = self.get_player_current_team(player, guild.id)
        if existing_team:
            await interaction.response.send_message(
                f"Offer failed: **{player.display_name}** is already signed to **{existing_team.get('name', 'another team')}**. They must submit a demand or be released first.",
                ephemeral=True
            )
            return

        roster_cap = settings["roster_cap"]
        current_roster = sum(1 for m in guild.members if target_team in m.roles)

        if current_roster >= roster_cap:
            await interaction.response.send_message(f"Roster limit reached! **{target_team.name}** is at maximum capacity ({current_roster}/{roster_cap}).", ephemeral=True)
            return

        embed = discord.Embed(
            title="Contract Proposal",
            description=f"{player.mention}, **{interaction.user.mention}** representing **{target_team.name}** has offered you a contract.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.set_author(name=settings["league_name"])

        view = self.ContractInteractiveView(target_player=player, manager=interaction.user, team_role=target_team, cog=self)

        try:
            await player.send(embed=embed, view=view)
            await interaction.response.send_message(f"Contract offer sent to {player.mention} via Direct Message.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(content=f"Could not DM {player.mention}. Posted offer in channel:", embed=embed, view=view)

    @app_commands.command(name="demand", description="Submit a release/transfer demand to leave your current club immediately.")
    @app_commands.describe(reason="Reason for departure")
    async def demand_command(self, interaction: discord.Interaction, reason: str):
        """Submits a demand. Blocks managers from demanding, strips leadership roles for Co-Managers/Captains, and returns player to Free Agency."""
        player = interaction.user
        guild = interaction.guild

        # 1. Block MANAGERS from demanding
        if self.is_strict_manager(player, guild.id):
            await interaction.response.send_message("Permission Denied: Managers cannot use `/demand`. You must be demoted or step down first.", ephemeral=True)
            return

        player_team_role, team_info = self.auto_detect_user_team(player, guild.id)

        if not player_team_role:
            await interaction.response.send_message("You are not currently assigned to any registered team roster.", ephemeral=True)
            return

        manager_member = self.get_team_manager(guild, player_team_role, team_info)

        # 2. Collect roles to remove (Team Role + Co Manager or Captain roles)
        roles_to_remove = [player_team_role]
        
        hc_role_id = self.db.get_role_id(guild.id, "head_coach")
        ac_role_id = self.db.get_role_id(guild.id, "assistant_coach")

        for r in player.roles:
            if (hc_role_id and r.id == hc_role_id) or (ac_role_id and r.id == ac_role_id):
                roles_to_remove.append(r)

        try:
            await player.remove_roles(*roles_to_remove, reason=f"Submitted transfer demand: {reason}")
            fa_role_id = self.db.get_role_id(guild.id, "free_agent")
            if fa_role_id:
                fa_role = guild.get_role(fa_role_id)
                if fa_role:
                    await player.add_roles(fa_role, reason="Reassigned to Free Agency on Transfer Demand")
        except discord.Forbidden:
            await interaction.response.send_message("Bot lacks permissions to adjust your roles.", ephemeral=True)
            return

        await interaction.response.send_message(f"You have submitted a demand and have been released from **{player_team_role.name}**.", ephemeral=True)

        if manager_member and manager_member.id != player.id:
            try:
                dm_embed = discord.Embed(
                    title="Player Demand Notice",
                    description=f"**{player.display_name}** ({player.mention}) has submitted a demand and departed from **{player_team_role.name}**.\n\n"
                                f"• **Reason:** {reason}",
                    color=discord.Color.from_rgb(220, 38, 38)
                )
                await manager_member.send(embed=dm_embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

        demands_ch_id = self.db.get_channel_id(guild.id, "demands")
        if demands_ch_id:
            demands_ch = guild.get_channel(demands_ch_id)
            if demands_ch:
                embed = discord.Embed(
                    title="Transfer Demand Submitted",
                    description=f"**{player.mention}** has demanded release from **{player_team_role.name}**.",
                    color=discord.Color.from_rgb(220, 38, 38)
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                await demands_ch.send(embed=embed)

    @app_commands.command(name="promote", description="Promote a player on your team roster to a management role.")
    @app_commands.describe(
        player="Player on your team roster to promote",
        role_type="Manager role level to assign"
    )
    @app_commands.choices(role_type=[
        app_commands.Choice(name="General Manager (Manager - Can Sign/Release)", value="gm"),
        app_commands.Choice(name="Head Coach (Co Manager - Can Sign/Release)", value="head_coach"),
        app_commands.Choice(name="Assistant Manager (Captain - Cannot Sign/Release)", value="assistant_coach")
    ])
    async def promote_command(self, interaction: discord.Interaction, player: discord.Member, role_type: app_commands.Choice[str]):
        """Promotes a player from team roster following strict franchise role hierarchy rules."""
        guild = interaction.guild
        promoter = interaction.user

        promoter_team, _ = self.auto_detect_user_team(promoter, guild.id)
        if not promoter_team:
            await interaction.response.send_message("You must belong to a franchise team roster to use `/promote`.", ephemeral=True)
            return

        if promoter_team not in player.roles:
            await interaction.response.send_message(f"**{player.display_name}** is not on your team (**{promoter_team.name}**) roster.", ephemeral=True)
            return

        owner_rid = self.db.get_role_id(guild.id, "owner")
        gm_rid = self.db.get_role_id(guild.id, "gm")
        hc_rid = self.db.get_role_id(guild.id, "head_coach")
        ac_rid = self.db.get_role_id(guild.id, "assistant_coach")

        user_rids = {r.id for r in promoter.roles}

        is_owner = owner_rid in user_rids if owner_rid else False
        is_gm_hc = (gm_rid in user_rids if gm_rid else False) or (hc_rid in user_rids if hc_rid else False)
        is_ac = ac_rid in user_rids if ac_rid else False

        if is_ac and not (is_owner or is_gm_hc or self.is_staff(promoter, guild.id)):
            await interaction.response.send_message("Permission Denied: Assistant Managers / Captains are not authorized to use `/promote`.", ephemeral=True)
            return

        if not (is_owner or is_gm_hc or self.is_staff(promoter, guild.id)):
            await interaction.response.send_message("Permission Denied: Only Franchise Owners or General Managers / Head Coaches can promote players.", ephemeral=True)
            return

        target_role_key = role_type.value
        if not is_owner and not self.is_staff(promoter, guild.id):
            if target_role_key in ["gm", "head_coach"]:
                await interaction.response.send_message("Permission Denied: General Managers and Head Coaches can only promote players to **Assistant Manager / Captain**.", ephemeral=True)
                return

        target_role_id = self.db.get_role_id(guild.id, target_role_key)
        if not target_role_id:
            await interaction.response.send_message(f"The role for `{role_type.name}` is not configured yet in `/panel` -> Roles.", ephemeral=True)
            return

        target_discord_role = guild.get_role(target_role_id)
        if not target_discord_role:
            await interaction.response.send_message("Configured role not found in server.", ephemeral=True)
            return

        try:
            await player.add_roles(target_discord_role, reason=f"Promoted by {promoter.display_name}")
            embed = discord.Embed(
                title="Management Promotion",
                description=f"{player.mention} (`{player.display_name}`) has been promoted to **{role_type.name}** for **{promoter_team.name}** by {promoter.mention}!",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("Bot lacks permissions to assign management roles.", ephemeral=True)

    @app_commands.command(name="demote", description="Demote a management member on your team roster.")
    @app_commands.describe(
        player="Management member on your team roster to demote",
        role_type="Specific management role to remove (or leave blank to strip all management roles)"
    )
    @app_commands.choices(role_type=[
        app_commands.Choice(name="General Manager (Manager)", value="gm"),
        app_commands.Choice(name="Head Coach (Co Manager)", value="head_coach"),
        app_commands.Choice(name="Assistant Manager (Captain)", value="assistant_coach")
    ])
    async def demote_command(
        self, 
        interaction: discord.Interaction, 
        player: discord.Member, 
        role_type: Optional[app_commands.Choice[str]] = None
    ):
        """Demotes a management member following strict franchise hierarchy permissions."""
        guild = interaction.guild
        demoter = interaction.user

        demoter_team, _ = self.auto_detect_user_team(demoter, guild.id)
        if not demoter_team:
            await interaction.response.send_message("You must belong to a franchise team roster to use `/demote`.", ephemeral=True)
            return

        if demoter_team not in player.roles:
            await interaction.response.send_message(f"**{player.display_name}** is not on your team (**{demoter_team.name}**) roster.", ephemeral=True)
            return

        owner_rid = self.db.get_role_id(guild.id, "owner")
        gm_rid = self.db.get_role_id(guild.id, "gm")
        hc_rid = self.db.get_role_id(guild.id, "head_coach")
        ac_rid = self.db.get_role_id(guild.id, "assistant_coach")

        user_rids = {r.id for r in demoter.roles}

        is_owner = owner_rid in user_rids if owner_rid else False
        is_gm_hc = (gm_rid in user_rids if gm_rid else False) or (hc_rid in user_rids if hc_rid else False)
        is_ac = ac_rid in user_rids if ac_rid else False

        if is_ac and not (is_owner or is_gm_hc or self.is_staff(demoter, guild.id)):
            await interaction.response.send_message("Permission Denied: Assistant Managers / Captains are not authorized to use `/demote`.", ephemeral=True)
            return

        if not (is_owner or is_gm_hc or self.is_staff(demoter, guild.id)):
            await interaction.response.send_message("Permission Denied: Only Franchise Owners or General Managers / Head Coaches can demote players.", ephemeral=True)
            return

        target_rids = {r.id for r in player.roles}

        target_is_owner = owner_rid in target_rids if owner_rid else False
        target_is_gm = gm_rid in target_rids if gm_rid else False
        target_is_hc = hc_rid in target_rids if hc_rid else False

        if target_is_owner and not self.is_staff(demoter, guild.id):
            await interaction.response.send_message("Permission Denied: You cannot demote a Franchise Owner.", ephemeral=True)
            return

        if (target_is_gm or target_is_hc) and not is_owner and not self.is_staff(demoter, guild.id):
            await interaction.response.send_message("Permission Denied: General Managers and Head Coaches can only demote **Assistant Managers / Captains**.", ephemeral=True)
            return

        roles_to_remove: List[discord.Role] = []
        role_names_removed: List[str] = []

        if role_type:
            target_key = role_type.value
            rid = self.db.get_role_id(guild.id, target_key)
            if rid:
                d_role = guild.get_role(rid)
                if d_role and d_role in player.roles:
                    roles_to_remove.append(d_role)
                    role_names_removed.append(role_type.name)
        else:
            all_mgr_keys = [("gm", "General Manager"), ("head_coach", "Head Coach"), ("assistant_coach", "Assistant Manager / Captain")]
            for k, title in all_mgr_keys:
                rid = self.db.get_role_id(guild.id, k)
                if rid:
                    d_role = guild.get_role(rid)
                    if d_role and d_role in player.roles:
                        roles_to_remove.append(d_role)
                        role_names_removed.append(title)

        if not roles_to_remove:
            await interaction.response.send_message(f"**{player.display_name}** does not hold any applicable management role to demote.", ephemeral=True)
            return

        try:
            await player.remove_roles(*roles_to_remove, reason=f"Demoted by {demoter.display_name}")
            removed_str = ", ".join(f"**{name}**" for name in role_names_removed)
            embed = discord.Embed(
                title="Management Demotion",
                description=f"{player.mention} (`{player.display_name}`) has been demoted from {removed_str} in **{demoter_team.name}** by {demoter.mention}.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("Bot lacks permissions to remove management roles.", ephemeral=True)

    @app_commands.command(name="release", description="Release a player from your franchise roster to Free Agency (Managers only).")
    @app_commands.describe(player="The player user to release")
    async def release_command(self, interaction: discord.Interaction, player: discord.Member):
        """Releases a player from team roster, notifies player via DM, and assigns Free Agent role."""
        guild = interaction.guild
        
        if not self.check_signing_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Only General Managers (Manager) and Head Coaches (Co-Manager) can release players.", ephemeral=True)
            return

        manager_team, team_info = self.auto_detect_user_team(interaction.user, guild.id)
        if not manager_team:
            await interaction.response.send_message("Could not identify your registered franchise team.", ephemeral=True)
            return

        if manager_team not in player.roles:
            await interaction.response.send_message(f"**{player.display_name}** is not currently on the **{manager_team.name}** roster.", ephemeral=True)
            return

        try:
            await player.remove_roles(manager_team, reason="Released from team roster")
            fa_role_id = self.db.get_role_id(guild.id, "free_agent")
            if fa_role_id:
                fa_role = guild.get_role(fa_role_id)
                if fa_role:
                    await player.add_roles(fa_role, reason="Reassigned to Free Agency on release")
        except discord.Forbidden:
            await interaction.response.send_message("Bot lacks permissions to update roles.", ephemeral=True)
            return

        settings = self.db.get_guild_settings(guild.id)
        emoji_str = f"{team_info.get('emoji', '')} " if team_info and team_info.get("emoji") else ""

        try:
            release_dm_embed = discord.Embed(
                title="You have been Released",
                description=f"You have been released in `{settings['league_name']}` by the {emoji_str}**{manager_team.name}**.",
                color=discord.Color.from_rgb(220, 38, 38)
            )
            release_dm_embed.set_author(name="Transactions")
            release_dm_embed.add_field(
                name="\u200b",
                value=f"• 👤 **Responsible Coach** - {interaction.user.mention} `{interaction.user.name}`",
                inline=False
            )
            
            if team_info and team_info.get("logo_url"):
                release_dm_embed.set_thumbnail(url=team_info["logo_url"])
            elif guild.icon:
                release_dm_embed.set_thumbnail(url=guild.icon.url)

            await player.send(embed=release_dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="Player Release Announcement",
            description=f"**{player.mention}** (`{player.display_name}`) has been officially released from {emoji_str}**{manager_team.name}**.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.response.send_message(embed=embed)

        tx_ch_id = self.db.get_channel_id(guild.id, "transactions")
        if tx_ch_id:
            tx_ch = guild.get_channel(tx_ch_id)
            if tx_ch:
                await tx_ch.send(embed=embed)

    @app_commands.command(name="trade", description="Execute a player swap trade between two franchise teams.")
    @app_commands.describe(
        player_giving="Player leaving your team",
        target_team="The target team involved in trade",
        player_receiving="Player joining your team"
    )
    async def trade_command(self, interaction: discord.Interaction, player_giving: discord.Member, target_team: discord.Role, player_receiving: discord.Member):
        """Executes a direct 1-for-1 player trade swap between two franchises."""
        guild = interaction.guild
        if not self.check_signing_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Only General Managers and Head Coaches can execute trades.", ephemeral=True)
            return

        my_team, _ = self.auto_detect_user_team(interaction.user, guild.id)
        if not my_team:
            await interaction.response.send_message("Could not auto-detect your franchise team.", ephemeral=True)
            return

        if my_team not in player_giving.roles:
            await interaction.response.send_message(f"**{player_giving.display_name}** is not on your team roster.", ephemeral=True)
            return

        if target_team not in player_receiving.roles:
            await interaction.response.send_message(f"**{player_receiving.display_name}** is not on **{target_team.name}** roster.", ephemeral=True)
            return

        try:
            await player_giving.remove_roles(my_team)
            await player_giving.add_roles(target_team)

            await player_receiving.remove_roles(target_team)
            await player_receiving.add_roles(my_team)
        except discord.Forbidden:
            await interaction.response.send_message("Bot lacks permissions to execute trade role swaps.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Official Trade Announcement",
            description=f"A player trade swap has been completed between **{my_team.name}** and **{target_team.name}**!\n\n"
                        f"• **{player_giving.mention}** ➔ **{target_team.name}**\n"
                        f"• **{player_receiving.mention}** ➔ **{my_team.name}**",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.response.send_message(embed=embed)

        tx_ch_id = self.db.get_channel_id(guild.id, "transactions")
        if tx_ch_id:
            tx_ch = guild.get_channel(tx_ch_id)
            if tx_ch:
                await tx_ch.send(embed=embed)

    @app_commands.command(name="freeagents", description="List current available Free Agents in the server.")
    async def freeagents_command(self, interaction: discord.Interaction):
        """Displays directory of free agent players in server."""
        guild = interaction.guild
        fa_role_id = self.db.get_role_id(guild.id, "free_agent")

        if not fa_role_id:
            await interaction.response.send_message("Free Agent role is not configured in `/panel` -> Roles.", ephemeral=True)
            return

        fa_role = guild.get_role(fa_role_id)
        if not fa_role:
            await interaction.response.send_message("Configured Free Agent role not found in server.", ephemeral=True)
            return

        fa_members = [m for m in guild.members if fa_role in m.roles]

        embed = discord.Embed(
            title="Free Agency Directory",
            description=f"Total Free Agents: **{len(fa_members)}**",
            color=discord.Color.from_rgb(220, 38, 38)
        )

        if fa_members:
            names = [f"• {m.mention} (`{m.display_name}`)" for m in fa_members[:25]]
            list_text = "\n".join(names)
            if len(fa_members) > 25:
                list_text += f"\n*...and {len(fa_members) - 25} more.*"
            embed.add_field(name="Available Players", value=list_text, inline=False)
        else:
            embed.add_field(name="Available Players", value="No players currently in Free Agency.", inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lfp", description="Post a recruitment advertisement targeting Free Agents (Managers Only).")
    @app_commands.describe(positions="Positions needed (e.g., GK, CB, ST)", message="Recruitment message details")
    async def lfp_command(self, interaction: discord.Interaction, positions: str, message: str):
        """Posts LFP post in #lfp channel targeting configured Free Agent role without mass pings."""
        guild = interaction.guild
        if not self.check_manager_permission(interaction.user, guild.id):
            await interaction.response.send_message("Permission Denied: Manager role required.", ephemeral=True)
            return

        my_team, team_info = self.auto_detect_user_team(interaction.user, guild.id)
        if not my_team:
            await interaction.response.send_message("Could not auto-detect your registered team.", ephemeral=True)
            return

        lfp_ch_id = self.db.get_channel_id(guild.id, "lfp")
        if not lfp_ch_id:
            await interaction.response.send_message("LFP Channel is not configured in `/panel` -> Channels.", ephemeral=True)
            return

        lfp_ch = guild.get_channel(lfp_ch_id)
        if not lfp_ch:
            await interaction.response.send_message("Configured LFP channel not found in server.", ephemeral=True)
            return

        fa_role_id = self.db.get_role_id(guild.id, "free_agent")
        fa_role = guild.get_role(fa_role_id) if fa_role_id else None
        mention_str = fa_role.mention if fa_role else ""

        emoji_str = f"{team_info.get('emoji', '')} " if team_info and team_info.get("emoji") else ""

        embed = discord.Embed(
            title=f"{emoji_str}{my_team.name} is Looking For Players!",
            description=message,
            color=discord.Color.from_rgb(220, 38, 38)
        )
        embed.add_field(name="Positions Needed", value=f"`{positions}`", inline=True)
        embed.add_field(name="Contact Manager", value=interaction.user.mention, inline=True)

        if team_info and team_info.get("logo_url"):
            embed.set_thumbnail(url=team_info["logo_url"])

        await lfp_ch.send(content=mention_str if mention_str else None, embed=embed)
        await interaction.response.send_message(f"Recruitment post published successfully in {lfp_ch.mention}.", ephemeral=True)

    @app_commands.command(name="transferwindow", description="Toggle the transfer market window open or closed globally (Staff Only).")
    @app_commands.describe(status="Open or Close the market window")
    @app_commands.choices(status=[
        app_commands.Choice(name="OPEN Market", value=1),
        app_commands.Choice(name="CLOSE Market", value=0)
    ])
    async def transferwindow_command(self, interaction: discord.Interaction, status: app_commands.Choice[int]):
        """Controls global transfer window status."""
        if not self.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message("Permission Denied: Staff role required.", ephemeral=True)
            return

        self.db.update_setting(interaction.guild_id, "transfer_window_open", status.value)
        status_text = "OPEN" if status.value == 1 else "CLOSED"

        embed = discord.Embed(
            title="Transfer Window Status Update",
            description=f"The Transfer Window is now officially **{status_text}**.",
            color=discord.Color.from_rgb(220, 38, 38)
        )
        await interaction.response.send_message(embed=embed)

        tx_ch_id = self.db.get_channel_id(interaction.guild_id, "transactions")
        if tx_ch_id:
            ch = interaction.guild.get_channel(tx_ch_id)
            if ch:
                await ch.send(embed=embed)

async def setup(bot: commands.Bot) -> None:
    """Extension load entry point."""
    await bot.add_cog(TLCTransferCog(bot))
