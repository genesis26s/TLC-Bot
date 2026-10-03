import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import re
import io
import asyncio
from datetime import datetime

CONFIG_FILE = "ticket_config.json"

DEFAULT_SERVER_CONFIG = {
    "embed": {
        "title": "TLC | Support Centre",
        "description": "Click on the dropdown selection menu below to choose the type of support ticket you would like to open.",
        "color": "2B2D31",
        "placeholder": "Select a type of ticket"
    },
    "categories": {
        "league_support": {
            "label": "League support",
            "desc": "This is if you have any questions about the league or if you need general help",
            "emoji": "⁉️",
            "role_id": None,
            "category_id": None,
            "welcome_title": "League support",
            "welcome_message": "Hello {user}! Thank you for contacting our League Support Team. A support agent will be with you shortly. Please explain your question in detail."
        },
        "player_report": {
            "label": "Player report",
            "desc": "This is if you would like to report one of our members/players",
            "emoji": "⚠️",
            "role_id": None,
            "category_id": None,
            "welcome_title": "Player report",
            "welcome_message": "Hello {user}! You have opened a Player Report. Please provide the player's username, a detailed description of the incident, and any evidence you have."
        },
        "application": {
            "label": "Application",
            "desc": "This is if you would like to apply to one of our applications!",
            "emoji": "💼",
            "role_id": None,
            "category_id": None,
            "welcome_title": "Application Support",
            "welcome_message": "Welcome {user}! We are excited to review your submission. Please state which role or application you are applying for."
        },
        "verification_support": {
            "label": "Verification support",
            "desc": "This is if you have a problem with verifying",
            "emoji": "✅",
            "role_id": None,
            "category_id": None,
            "welcome_title": "Verification support",
            "welcome_message": "Hello {user}! If you are having trouble verifying, please explain the issue and provide any screenshots of errors."
        }
    }
}

# ── PER-GUILD CONFIGURATION ENGINE ──────────────────────────────────────────

def load_master_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=4, ensure_ascii=False)
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_master_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def load_guild_config(guild_id: int) -> dict:
    master = load_master_config()
    str_gid = str(guild_id)
    if str_gid not in master:
        return json.loads(json.dumps(DEFAULT_SERVER_CONFIG))
    return master[str_gid]

def save_guild_config(guild_id: int, guild_config: dict):
    master = load_master_config()
    master[str(guild_id)] = guild_config
    save_master_config(master)

# ── SHARED TICKET ACTION HELPERS ────────────────────────────────────────────

async def process_ticket_claim(interaction_or_ctx, user: discord.Member, channel: discord.TextChannel):
    """Executes the ticket claim logic for both buttons and slash commands."""
    if not user.guild_permissions.manage_channels:
        msg = "❌ Only support staff/admins with 'Manage Channels' permission can claim tickets."
        if isinstance(interaction_or_ctx, discord.Interaction):
            return await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        return await interaction_or_ctx.send(msg, ephemeral=True)

    embed = discord.Embed(
        title="🎫 Ticket Claimed",
        description=f"This ticket is now being handled by {user.mention}.",
        color=discord.Color.blue()
    )

    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)

async def process_ticket_close(interaction_or_ctx, user: discord.Member, channel: discord.TextChannel, owner_id: int = None):
    """Executes channel validation, transcript creation, and channel deletion."""
    # Safety Check: Verify channel is actually a ticket
    is_ticket = (
        channel.name.startswith("ticket-") or 
        "-" in channel.name or 
        (channel.topic and "Ticket Owner:" in channel.topic)
    )
    if not is_ticket:
        msg = "❌ This command can only be executed inside active support ticket channels."
        if isinstance(interaction_or_ctx, discord.Interaction):
            return await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        return await interaction_or_ctx.send(msg, ephemeral=True)

    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.defer()

    guild = channel.guild

    # Fallback to topic metadata if owner_id not supplied directly
    if not owner_id and channel.topic and "Ticket Owner:" in channel.topic:
        try:
            owner_id = int(channel.topic.split("Ticket Owner:")[1].strip())
        except ValueError:
            pass

    # 1. Generate Transcript
    transcript_text = f"=========================================\n"
    transcript_text += f" TLC BOT TICKET TRANSCRIPT\n"
    transcript_text += f" Server: {guild.name} ({guild.id})\n"
    transcript_text += f" Channel: #{channel.name}\n"
    transcript_text += f" Closed By: {user} ({user.id})\n"
    transcript_text += f" Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    transcript_text += f"=========================================\n\n"

    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        messages.append(msg)

    for msg in messages:
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        transcript_text += f"[{timestamp}] {msg.author} ({msg.author.id}):\n"
        if msg.content:
            transcript_text += f"  {msg.content}\n"
        if msg.attachments:
            for att in msg.attachments:
                transcript_text += f"  [Attachment: {att.url}]\n"
        transcript_text += "\n"

    transcript_file = discord.File(
        fp=io.BytesIO(transcript_text.encode('utf-8')),
        filename=f"transcript-{channel.name}.txt"
    )

    # Dispatch transcript DM to ticket owner
    if owner_id:
        owner = guild.get_member(owner_id)
        if owner:
            try:
                dm_embed = discord.Embed(
                    title="🔒 Ticket Closed",
                    description=f"Your ticket in **{guild.name}** (`#{channel.name}`) has been closed. Attached is your chat transcript.",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                dm_file = discord.File(
                    fp=io.BytesIO(transcript_text.encode('utf-8')),
                    filename=f"transcript-{channel.name}.txt"
                )
                await owner.send(embed=dm_embed, file=dm_file)
            except Exception:
                pass

    # 2. Deletion Countdown
    embed = discord.Embed(
        title="🔒 Closing Ticket",
        description="Transcript generated! This channel will be **deleted in 5 seconds**...",
        color=discord.Color.red()
    )
    await channel.send(embed=embed, file=transcript_file)
    
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"TLC Ticket closed by {user}")
    except Exception:
        pass

# ── Ticket Action Buttons inside Ticket Channels ─────────────────────────────

class TicketActionView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="tlc_claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_ticket_claim(interaction, interaction.user, interaction.channel)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="tlc_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_ticket_close(interaction, interaction.user, interaction.channel, self.owner_id)

# ── Public Dropdown Selector (Scoped to interaction.guild_id) ───────────────

class DynamicTicketSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        config = load_guild_config(guild_id)
        options = []
        
        for key, cat in config["categories"].items():
            options.append(
                discord.SelectOption(
                    label=cat["label"],
                    description=cat["desc"][:100],
                    emoji=cat["emoji"],
                    value=key
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No categories configured",
                    description="An admin needs to add categories for this server.",
                    emoji="❌",
                    value="none"
                )
            )

        super().__init__(
            placeholder=config["embed"]["placeholder"],
            min_values=1,
            max_values=1,
            options=options,
            custom_id="tlc_dynamic_ticket_select"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        if selected_value == "none":
            await interaction.response.send_message("❌ This option is placeholder-only.", ephemeral=True)
            return

        config = load_guild_config(interaction.guild_id)
        ticket_data = config["categories"].get(selected_value)
        if not ticket_data:
            await interaction.response.send_message("❌ Category not configured for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        category = guild.get_channel(ticket_data["category_id"]) if ticket_data.get("category_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        staff_role = guild.get_role(ticket_data["role_id"]) if ticket_data.get("role_id") else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)

        clean_name = re.sub(r'[^a-zA-Z0-9-]', '', selected_value.replace('_', '-'))
        channel_name = f"{clean_name}-{user.name.lower()}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"Ticket Owner: {user.id}",  # Topic metadata for /close command
                overwrites=overwrites,
                reason=f"TLC Ticket opened by {user}"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to create channels in this server.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create ticket channel: {str(e)}", ephemeral=True)
            return

        color_hex = config["embed"]["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()

        embed = discord.Embed(
            title=ticket_data["welcome_title"],
            description=ticket_data["welcome_message"].replace("{user}", user.mention),
            color=embed_color
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"TLC Bot Ticket Support • {user.name}")

        ping_content = user.mention
        if staff_role:
            ping_content += f" {staff_role.mention}"

        action_view = TicketActionView(owner_id=user.id)
        await ticket_channel.send(content=ping_content, embed=embed, view=action_view)
        await interaction.followup.send(f"✅ Ticket created successfully! Go to {ticket_channel.mention}", ephemeral=True)

class DynamicTicketView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.add_item(DynamicTicketSelect(guild_id))

# ── Modals & Interactive Selector Views for `/ticket_panel` Dashboard ──────

class CategoryModal(discord.ui.Modal):
    def __init__(self, guild_id: int, cat_id: str = "", existing_data: dict = None):
        title_str = f"Edit: {cat_id}" if existing_data else "Add Ticket Category"
        super().__init__(title=title_str)
        self.guild_id = guild_id

        self.cat_id_input = discord.ui.TextInput(
            label="Category ID (Key)",
            placeholder="e.g. league_support, player_report",
            default=cat_id,
            required=True
        )
        self.label_input = discord.ui.TextInput(
            label="Label (Title in Dropdown)",
            placeholder="e.g. League Support",
            default=existing_data.get("label", "") if existing_data else "",
            required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="Description",
            placeholder="Short overview of what this ticket type is for",
            default=existing_data.get("desc", "") if existing_data else "",
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.category_channel_id_input = discord.ui.TextInput(
            label="Target Discord Category Channel ID",
            placeholder="ID of Discord category channel (or leave empty)",
            default=str(existing_data.get("category_id") or "") if existing_data else "",
            required=False
        )
        self.staff_role_id_input = discord.ui.TextInput(
            label="Staff Role ID (Ping on Creation)",
            placeholder="Role ID to ping (or leave empty)",
            default=str(existing_data.get("role_id") or "") if existing_data else "",
            required=False
        )

        self.add_item(self.cat_id_input)
        self.add_item(self.label_input)
        self.add_item(self.desc_input)
        self.add_item(self.category_channel_id_input)
        self.add_item(self.staff_role_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = load_guild_config(self.guild_id)
        clean_id = self.cat_id_input.value.lower().replace(" ", "_")

        cat_ch_id = None
        if self.category_channel_id_input.value.strip():
            try:
                cat_ch_id = int(self.category_channel_id_input.value.strip())
            except ValueError:
                return await interaction.response.send_message("❌ Target Category Channel ID must be a valid numeric ID.", ephemeral=True)

        staff_r_id = None
        if self.staff_role_id_input.value.strip():
            try:
                staff_r_id = int(self.staff_role_id_input.value.strip())
            except ValueError:
                return await interaction.response.send_message("❌ Staff Role ID must be a valid numeric ID.", ephemeral=True)

        existing = config["categories"].get(clean_id, {})
        config["categories"][clean_id] = {
            "label": self.label_input.value,
            "desc": self.desc_input.value,
            "emoji": existing.get("emoji", "🎫"),
            "role_id": staff_r_id,
            "category_id": cat_ch_id,
            "welcome_title": self.label_input.value,
            "welcome_message": existing.get("welcome_message", "Hello {user}! How can we help?")
        }
        save_guild_config(self.guild_id, config)
        await interaction.response.send_message(f"✅ Ticket category `{clean_id}` saved for **{interaction.guild.name}**!", ephemeral=True)

class CategoryEditSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        config = load_guild_config(guild_id)
        options = []
        for key, value in config["categories"].items():
            options.append(discord.SelectOption(
                label=value["label"],
                value=key,
                description=value["desc"][:100],
                emoji=value["emoji"]
            ))
        
        if not options:
            options.append(discord.SelectOption(label="No categories to edit", value="none"))

        super().__init__(placeholder="Select a category to edit...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("❌ No categories available.", ephemeral=True)

        config = load_guild_config(self.guild_id)
        cat_id = self.values[0]
        cat_data = config["categories"].get(cat_id)
        if not cat_data:
            return await interaction.response.send_message("❌ Category data not found for this server.", ephemeral=True)

        modal = CategoryModal(guild_id=self.guild_id, cat_id=cat_id, existing_data=cat_data)
        await interaction.response.send_modal(modal)

class CategoryEditView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.add_item(CategoryEditSelect(guild_id))

class EditPanelModal(discord.ui.Modal, title="Customize Support Panel"):
    def __init__(self, guild_id: int, current_embed: dict):
        super().__init__()
        self.guild_id = guild_id
        self.title_input = discord.ui.TextInput(
            label="Panel Embed Title",
            default=current_embed.get("title", "TLC | Support Centre"),
            required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="Panel Embed Description",
            default=current_embed.get("description", ""),
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.placeholder_input = discord.ui.TextInput(
            label="Dropdown Placeholder Text",
            default=current_embed.get("placeholder", "Select a type of ticket"),
            required=True
        )
        self.color_input = discord.ui.TextInput(
            label="Hex Color (without #)",
            default=current_embed.get("color", "2B2D31"),
            required=True,
            max_length=6
        )

        self.add_item(self.title_input)
        self.add_item(self.desc_input)
        self.add_item(self.placeholder_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = load_guild_config(self.guild_id)
        config["embed"]["title"] = self.title_input.value
        config["embed"]["description"] = self.desc_input.value
        config["embed"]["placeholder"] = self.placeholder_input.value
        config["embed"]["color"] = self.color_input.value.replace("#", "")
        save_guild_config(self.guild_id, config)
        await interaction.response.send_message("✅ Support panel embed updated for this server!", ephemeral=True)

class CategoryDeleteSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        config = load_guild_config(guild_id)
        options = []
        for key, value in config["categories"].items():
            options.append(discord.SelectOption(label=value["label"], value=key, emoji=value["emoji"]))
        
        if not options:
            options.append(discord.SelectOption(label="No categories to delete", value="none"))

        super().__init__(placeholder="Select a category to delete...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("❌ No category selected.", ephemeral=True)

        config = load_guild_config(self.guild_id)
        key = self.values[0]
        if key in config["categories"]:
            del config["categories"][key]
            save_guild_config(self.guild_id, config)
            await interaction.response.send_message(f"✅ Deleted category `{key}` from **{interaction.guild.name}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Category `{key}` not found.", ephemeral=True)

class CategoryDeleteView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.add_item(CategoryDeleteSelect(guild_id))

class PanelDashboardView(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Add Category", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_cat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CategoryModal(guild_id=self.guild_id))

    @discord.ui.button(label="Edit Category", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def edit_cat(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_guild_config(self.guild_id)
        if not config["categories"]:
            return await interaction.response.send_message("❌ No categories available to edit in this server.", ephemeral=True)
        await interaction.response.send_message("Select a category below to edit:", view=CategoryEditView(self.guild_id), ephemeral=True)

    @discord.ui.button(label="Delete Category", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete_cat(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_guild_config(self.guild_id)
        if not config["categories"]:
            return await interaction.response.send_message("❌ No categories available to delete in this server.", ephemeral=True)
        await interaction.response.send_message("Select a category below to delete:", view=CategoryDeleteView(self.guild_id), ephemeral=True)

    @discord.ui.button(label="Edit Panel Embed", style=discord.ButtonStyle.secondary, emoji="🎨", row=1)
    async def edit_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_guild_config(self.guild_id)
        await interaction.response.send_modal(EditPanelModal(guild_id=self.guild_id, current_embed=config["embed"]))

    @discord.ui.button(label="View Categories", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def view_cats(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_guild_config(self.guild_id)
        if not config["categories"]:
            return await interaction.response.send_message("No categories set up yet for this server.", ephemeral=True)

        embed = discord.Embed(title=f"Current Categories for {interaction.guild.name}", color=discord.Color.blue())
        for key, value in config["categories"].items():
            role_text = f"<@&{value['role_id']}>" if value.get("role_id") else "None"
            cat_text = f"<#{value['category_id']}>" if value.get("category_id") else "Default"
            embed.add_field(
                name=f"{value['emoji']} {value['label']} (`{key}`)",
                value=f"**Desc:** {value['desc']}\n**Pings:** {role_text}\n**Category Channel:** {cat_text}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Post Ticket Panel", style=discord.ButtonStyle.success, emoji="🚀", row=1)
    async def post_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_guild_config(self.guild_id)
        embed_data = config["embed"]
        color_hex = embed_data["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()

        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data["description"],
            color=embed_color
        )

        view = DynamicTicketView(self.guild_id)
        await interaction.response.send_message("Posting public support ticket panel for this server...", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)

# ── Main Cog Definition ──────────────────────────────────────────────────────

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"[{self.__class__.__name__}] TLC Guild-Isolated Ticket Management System Online.")

    # 1. TICKET PANEL COMMAND (/ticket_panel)
    @commands.hybrid_command(name="ticket_panel", description="Open the interactive Ticket Management Dashboard for this server.")
    @commands.has_permissions(administrator=True)
    async def ticket_panel(self, ctx: commands.Context):
        config = load_guild_config(ctx.guild.id)
        categories_count = len(config["categories"])

        embed = discord.Embed(
            title=f"⚙️ TLC Ticket Dashboard | {ctx.guild.name}",
            description=(
                "Welcome to the **Ticket Admin Control Centre**!\n"
                "Use the interactive buttons below to fully customize your support system, add, edit, or remove ticket types, or post the public panel."
            ),
            color=discord.Color.dark_theme()
        )
        embed.add_field(name="Active Categories", value=f"`{categories_count}` configured", inline=True)
        embed.add_field(name="Panel Theme Color", value=f"`#{config['embed']['color']}`", inline=True)
        embed.set_footer(text="TLC Bot • Server-Isolated Support System")

        view = PanelDashboardView(self.bot, ctx.guild.id)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    # 2. CLOSE COMMAND (/close)
    @app_commands.command(name="close", description="Close the current support ticket channel and generate a transcript.")
    async def close_command(self, interaction: discord.Interaction):
        await process_ticket_close(interaction, interaction.user, interaction.channel)

    # 3. CLAIM COMMAND (/claim)
    @app_commands.command(name="claim", description="Claim the current support ticket to mark it as handled by you.")
    async def claim_command(self, interaction: discord.Interaction):
        await process_ticket_claim(interaction, interaction.user, interaction.channel)

    # 4. ADD USER COMMAND (/add)
    @app_commands.command(name="add", description="Add a user to the current support ticket.")
    @app_commands.describe(user="The member you want to add to this ticket")
    async def add_command(self, interaction: discord.Interaction, user: discord.Member):
        channel = interaction.channel

        is_ticket = (
            channel.name.startswith("ticket-") or 
            "-" in channel.name or 
            (channel.topic and "Ticket Owner:" in channel.topic)
        )
        if not is_ticket:
            return await interaction.response.send_message(
                "❌ This command can only be executed inside active support ticket channels.", 
                ephemeral=True
            )

        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "❌ Only support staff with 'Manage Channels' permission can add users to tickets.", 
                ephemeral=True
            )

        try:
            await channel.set_permissions(
                user,
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                reason=f"Added to ticket by {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ I do not have permissions to modify channel permissions.", 
                ephemeral=True
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ An error occurred: {str(e)}", 
                ephemeral=True
            )

        embed = discord.Embed(
            title="👤 User Added",
            description=f"{user.mention} has been added to this ticket by {interaction.user.mention}.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
