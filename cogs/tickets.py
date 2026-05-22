import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import re

CONFIG_FILE = "ticket_config.json"

# Default configuration mirroring your screenshot design
DEFAULT_CONFIG = {
    "embed": {
        "title": "VRA | Support centre",
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
            "desc": "This is you would like to report one of our members/players",
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


def load_config() -> dict:
    """Loads the ticket configurations from the JSON file."""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG


def save_config(config: dict):
    """Saves the ticket configuration to the JSON file."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


class DynamicTicketSelect(discord.ui.Select):
    def __init__(self):
        config = load_config()
        options = []
        
        # Build select dropdown options dynamically from the saved categories
        for key, cat in config["categories"].items():
            options.append(
                discord.SelectOption(
                    label=cat["label"],
                    description=cat["desc"][:100],  # Max 100 character limit in Discord API
                    emoji=cat["emoji"],
                    value=key
                )
            )

        # Fallback if no categories exist
        if not options:
            options.append(
                discord.SelectOption(
                    label="No categories configured",
                    description="An admin needs to add categories.",
                    emoji="❌",
                    value="none"
                )
            )

        super().__init__(
            placeholder=config["embed"]["placeholder"],
            min_values=1,
            max_values=1,
            options=options,
            custom_id="tlc_dynamic_ticket_select"  # Keeps persistent across bot restarts
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        if selected_value == "none":
            await interaction.response.send_message("❌ This option is placeholder-only.", ephemeral=True)
            return

        config = load_config()
        ticket_data = config["categories"].get(selected_value)
        if not ticket_data:
            await interaction.response.send_message("❌ Configuration error. Category not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        # Get target category channel
        category = guild.get_channel(ticket_data["category_id"]) if ticket_data["category_id"] else None

        # Set permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # Grant support role permissions if configured
        staff_role = guild.get_role(ticket_data["role_id"]) if ticket_data["role_id"] else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)

        # Create ticket channel name
        clean_name = re.sub(r'[^a-zA-Z0-9-]', '', selected_value.replace('_', '-'))
        channel_name = f"{clean_name}-{user.name.lower()}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"TLC Ticket opened by {user}"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to create channels.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create ticket channel: {str(e)}", ephemeral=True)
            return

        # Send welcome message in the channel
        color_hex = config["embed"]["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()

        embed = discord.Embed(
            title=ticket_data["welcome_title"],
            description=ticket_data["welcome_message"].replace("{user}", user.mention),
            color=embed_color
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"TLC-Bot Ticket Support • {user.name}")

        ping_content = user.mention
        if staff_role:
            ping_content += f" {staff_role.mention}"

        await ticket_channel.send(content=ping_content, embed=embed)
        await interaction.followup.send(f"✅ Ticket created successfully! Go to {ticket_channel.mention}", ephemeral=True)


class DynamicTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Infinite timeout makes the view persistent
        self.add_item(DynamicTicketSelect())


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Register the persistent dropdown view
        self.bot.add_view(DynamicTicketView())
        print(f"[{self.__class__.__name__}] Dynamic Ticket System Registered.")

    # Main setup Command
    @app_commands.command(name="setup_tickets", description="Post the customizable ticket system panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        config = load_config()
        embed_data = config["embed"]
        color_hex = embed_data["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()

        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data["description"],
            color=embed_color
        )
        
        view = DynamicTicketView()
        await interaction.response.send_message("Creating support ticket panel...", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)

    # Config commands group
    ticket_config = app_commands.Group(name="ticket_config", description="Configure custom ticket panel settings.")

    @ticket_config.command(name="add_category", description="Add or edit a ticket category.")
    @app_commands.describe(
        id="A unique short identifier (e.g., league_support, report)",
        label="The title seen in the selection option",
        desc="A short description of this ticket type",
        emoji="An emoji representing this ticket type (e.g. ⚠️, ⁉️)",
        role="The staff/support role to ping upon ticket creation",
        category_channel="The category channel where tickets should be created",
        welcome_title="The title of the welcome embed inside the ticket",
        welcome_message="The message sent inside the ticket (use {user} to mention the creator)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def config_add(
        self,
        interaction: discord.Interaction,
        id: str,
        label: str,
        desc: str,
        emoji: str,
        role: discord.Role = None,
        category_channel: discord.CategoryChannel = None,
        welcome_title: str = "Ticket Support Desk",
        welcome_message: str = "Hello {user}! Please wait while our representatives review your ticket."
    ):
        config = load_config()
        clean_id = id.lower().replace(" ", "_")

        config["categories"][clean_id] = {
            "label": label,
            "desc": desc,
            "emoji": emoji,
            "role_id": role.id if role else None,
            "category_id": category_channel.id if category_channel else None,
            "welcome_title": welcome_title,
            "welcome_message": welcome_message
        }
        
        save_config(config)
        await interaction.response.send_message(
            f"✅ Successfully configured/updated category `{clean_id}`!\n"
            f"Run `/setup_tickets` to post the updated panel.",
            ephemeral=True
        )

    @ticket_config.command(name="remove_category", description="Remove an existing ticket category.")
    @app_commands.describe(id="The short identifier of the category to delete")
    @app_commands.checks.has_permissions(administrator=True)
    async def config_remove(self, interaction: discord.Interaction, id: str):
        config = load_config()
        clean_id = id.lower()

        if clean_id in config["categories"]:
            del config["categories"][clean_id]
            save_config(config)
            await interaction.response.send_message(f"✅ Removed ticket category option `{clean_id}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Category `{clean_id}` was not found.", ephemeral=True)

    @ticket_config.command(name="list_categories", description="List all active ticket categories.")
    @app_commands.checks.has_permissions(administrator=True)
    async def config_list(self, interaction: discord.Interaction):
        config = load_config()
        if not config["categories"]:
            await interaction.response.send_message("No categories currently set up.", ephemeral=True)
            return

        embed = discord.Embed(title="Current Ticket Categories", color=discord.Color.blue())
        for key, value in config["categories"].items():
            role_text = f"<@&{value['role_id']}>" if value["role_id"] else "None"
            cat_text = f"<#{value['category_id']}>" if value["category_id"] else "Default (None)"
            
            embed.add_field(
                name=f"{value['emoji']} {value['label']} (`{key}`)",
                value=f"**Description:** {value['desc']}\n**Pings:** {role_text}\n**Category:** {cat_text}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket_config.command(name="edit_panel", description="Customize the appearance of the main support panel.")
    @app_commands.describe(
        title="Main support panel title",
        description="Main support panel description instructions",
        placeholder="Dropdown selector placeholder text",
        color_hex="Embed theme color (Hex string, e.g., 5865F2)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def config_edit_panel(
        self,
        interaction: discord.Interaction,
        title: str = None,
        description: str = None,
        placeholder: str = None,
        color_hex: str = None
    ):
        config = load_config()
        if title:
            config["embed"]["title"] = title
        if description:
            config["embed"]["description"] = description
        if placeholder:
            config["embed"]["placeholder"] = placeholder
        if color_hex:
            config["embed"]["color"] = color_hex.replace("#", "")

        save_config(config)
        await interaction.response.send_message("✅ Main support panel configurations updated!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
