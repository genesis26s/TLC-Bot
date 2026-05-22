import discord
from discord.ext import commands
from discord import app_commands
import json
import os

# Define the Ticket configuration structure matching your design
TICKET_CONFIG = {
    "embed": {
        "title": "VRA | Support centre",
        "description": "Click on the dropdown selection menu below to choose the type of support ticket you would like to open.",
        "color": "2B2D31",
        "placeholder": "Select a type of ticket"
    },
    "options": {
        "league_support": {
            "label": "League support",
            "desc": "This is if you have any questions about the league or if you need general help",
            "emoji": "⁉️",
            "role_id": 112233445566778899,       # Replace with your actual Staff role ID
            "category_id": 998877665544332211,   # Replace with your actual Category ID
            "welcome_title": "League Support Ticket Requested",
            "welcome_msg": "Hello {user}! Thank you for contacting our League Support Team. A support agent representing the League Staff will be with you shortly. Please explain your question or concern in detail."
        },
        "player_report": {
            "label": "Player report",
            "desc": "This is you would like to report one of our members/players",
            "emoji": "⚠️",
            "role_id": 223344556677889900,       # Replace with your actual Staff role ID
            "category_id": 887766554433221100,   # Replace with your actual Category ID
            "welcome_title": "Report Filing Panel",
            "welcome_msg": "Hello {user}! You have requested a player report ticket. To help us process this swiftly, please provide:\n\n1. Username of offender\n2. Detailed description of the violation\n3. Evidence (screenshots/video links)"
        },
        "application": {
            "label": "Application",
            "desc": "This is if you would like to apply to one of our applications!",
            "emoji": "💼",
            "role_id": 334455667788990011,       # Replace with your actual Staff role ID
            "category_id": 776655443322110011,   # Replace with your actual Category ID
            "welcome_title": "Application Assessment Channel",
            "welcome_msg": "Welcome {user}! We are excited to review your submission. Please state which role/application you are applying for and paste your application link or submit your pitch below."
        },
        "verification_support": {
            "label": "Verification support",
            "desc": "This is if you have a problem with verifying",
            "emoji": "✅",
            "role_id": 445566778899001122,       # Replace with your actual Staff role ID
            "category_id": 665544332211001122,   # Replace with your actual Category ID
            "welcome_title": "Verification Desk",
            "welcome_msg": "Hello {user}! If you are having trouble verifying your profile, please provide us with your discord username and a screenshot of any error messages you are receiving."
        }
    }
}

class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = []
        # Dynamically build options from config dictionary
        for key, opt in TICKET_CONFIG["options"].items():
            options.append(
                discord.SelectOption(
                    label=opt["label"],
                    description=opt["desc"][:100], # Discord limits descriptions to 100 chars
                    emoji=opt["emoji"],
                    value=key
                )
            )
        
        super().__init__(
            placeholder=TICKET_CONFIG["embed"]["placeholder"],
            min_values=1,
            max_values=1,
            options=options,
            custom_id="tlc_ticket_select" # CRITICAL: Allows view persistence across bot restarts
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        guild = interaction.guild
        user = interaction.user

        # Fetch custom data parameters for this selected option
        ticket_data = TICKET_CONFIG["options"].get(selected_value)
        if not ticket_data:
            await interaction.response.send_message("❌ Configuration error for this category.", ephemeral=True)
            return

        # Defer immediately since creating channels can take more than 3 seconds
        await interaction.response.defer(ephemeral=True)

        # Get parent category if configured
        category = guild.get_channel(ticket_data["category_id"]) if ticket_data["category_id"] else None

        # Build dynamic permission overrides
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # Grant explicit view & send permission to staff role if exists
        staff_role = guild.get_role(ticket_data["role_id"]) if ticket_data["role_id"] else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)

        # Build clean name formatting (e.g., league-support-username)
        channel_name = f"{selected_value.replace('_', '-')}-{user.name.lower()}"

        try:
            # Create the ticket text channel
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"TLC-Bot Ticket creation: {ticket_data['label']} by {user}"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permissions to manage channels or create tickets.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create ticket channel: {str(e)}", ephemeral=True)
            return

        # Build welcome greeting embed matching the customized aesthetic
        color_hex = TICKET_CONFIG["embed"]["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()
        
        welcome_embed = discord.Embed(
            title=ticket_data["welcome_title"],
            description=ticket_data["welcome_msg"].replace("{user}", user.mention),
            color=embed_color
        )
        welcome_embed.set_footer(text=f"TLC-Bot Ticket Support Desk • {user.name}")
        welcome_embed.set_thumbnail(url=user.display_avatar.url)

        # Mention the user and ping staff role inside channel
        ping_content = f"{user.mention}"
        if staff_role:
            ping_content += f" {staff_role.mention}"

        await ticket_channel.send(content=ping_content, embed=welcome_embed)
        await interaction.followup.send(f"✅ Ticket created successfully! Go to {ticket_channel.mention}", ephemeral=True)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Timeout=None makes it completely persistent
        self.add_item(TicketSelect())


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Re-register views dynamically so buttons/menus continue working on reconnects
        self.bot.add_view(TicketView())
        print(f"[{self.__class__.__name__}] Persistent Views Registered.")

    @app_commands.command(name="setup_tickets", description="Post the support center ticket setup embed panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        """Sends the initial interactive ticket setup embed layout to a channel."""
        embed_data = TICKET_CONFIG["embed"]
        color_hex = embed_data["color"]
        embed_color = discord.Color.from_str(f"#{color_hex}") if color_hex else discord.Color.blurple()

        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data["description"],
            color=embed_color
        )
        
        view = TicketView()
        
        # We answer the interaction ephemerally first to avoid "Interaction Failed" errors
        await interaction.response.send_message("Creating support ticket panel setup...", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
