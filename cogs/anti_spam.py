import discord
from discord.ext import commands
from collections import defaultdict, deque
import time
import re
import asyncio
from datetime import timedelta
import logging

from cogs.security_utils import (
    get_guild_security_config,
    is_whitelisted,
    can_moderate_user,
    log_security_event
)

logger = logging.getLogger("TLCBot.AntiSpam")

class AntiSpamCog(commands.Cog):
    """Lightweight anti-spam protection monitoring floods, repeated/similar text, and mention/link bursts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Rolling message history: { (guild_id, user_id): deque([(timestamp, normalized_content)...]) }
        self.msg_history = defaultdict(lambda: deque(maxlen=15))
        self.cooldowns = set()

    def normalize_text(self, text: str) -> str:
        """Lightweight normalization: lowercase, strip punctuation and collapse whitespace."""
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        return " ".join(text.split())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs, Webhooks, or Bot accounts
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        author = message.author
        cfg = get_guild_security_config(guild.id)

        if not cfg.get("enabled") or not cfg["anti_spam"]["enabled"]:
            return

        # Check Whitelist, Channel Exemptions & Permissions
        if message.channel.id in cfg.get("exempt_channels", []):
            return
        if is_whitelisted(author, cfg):
            return

        spam_cfg = cfg["anti_spam"]
        now = time.time()
        norm_text = self.normalize_text(message.content)

        # Record message
        user_history = self.msg_history[(guild.id, author.id)]
        user_history.append((now, norm_text))

        # Filter window
        window = spam_cfg["message_window"]
        recent = [item for item in user_history if item[0] >= now - window]

        trigger_type = None

        # 1. Message Flood Check
        if len(recent) >= spam_cfg["message_limit"]:
            trigger_type = f"Message Flood ({len(recent)} msg / {window}s)"

        # 2. Duplicate / Similar Message Check
        elif norm_text and len(norm_text) > 3:
            dup_window = spam_cfg["duplicate_window"]
            dup_recent = [item for item in user_history if item[0] >= now - dup_window and item[1] == norm_text]
            if len(dup_recent) >= spam_cfg["duplicate_limit"]:
                trigger_type = f"Repeated Message Spam ({len(dup_recent)} duplicate messages)"

        # 3. Excessive Mentions
        elif len(message.mentions) >= spam_cfg["mention_limit"]:
            trigger_type = f"Excessive Mentions ({len(message.mentions)} user mentions)"

        # 4. Excessive Links
        elif len(re.findall(r'https?://\S+', message.content)) >= spam_cfg["link_limit"]:
            trigger_type = f"Excessive Links ({len(re.findall(r'https?://\S+', message.content))} URLs)"

        # 5. Character Flooding
        elif len(message.content) >= spam_cfg["character_limit"]:
            trigger_type = f"Character Flooding ({len(message.content)} characters)"

        # Apply Mitigation if Triggered
        if trigger_type:
            await self.apply_antispam_penalty(message, trigger_type, spam_cfg)

    async def apply_antispam_penalty(self, message: discord.Message, trigger_type: str, spam_cfg: dict):
        guild = message.guild
        author = message.author
        cd_key = (guild.id, author.id)

        # 1. Always purge triggering message
        try:
            await message.delete()
        except Exception:
            pass

        if cd_key in self.cooldowns:
            return
        self.cooldowns.add(cd_key)

        action = spam_cfg["action"].lower()
        timeout_mins = spam_cfg.get("timeout_duration_minutes", 15)

        if not can_moderate_user(guild, guild.me, author):
            action = "delete"

        result_str = "Message Deleted"

        try:
            if action == "warn":
                try:
                    await author.send(f"⚠️ **Anti-Spam Warning:** Your message in **{guild.name}** was deleted for `{trigger_type}`.")
                except Exception:
                    pass
                result_str = "Message Deleted + User Warned"

            elif action == "timeout":
                await author.timeout(timedelta(minutes=timeout_mins), reason=f"Anti-Spam: {trigger_type}")
                result_str = f"Message Deleted + Timed out for {timeout_mins}m"

            elif action == "kick":
                await author.kick(reason=f"Anti-Spam: {trigger_type}")
                result_str = "Message Deleted + User Kicked"

            elif action == "ban":
                await author.ban(reason=f"Anti-Spam: {trigger_type}", delete_message_days=1)
                result_str = "Message Deleted + User Banned"

        except Exception as e:
            result_str = f"Action Error ({str(e)})"

        # Log Security Incident
        await log_security_event(
            guild,
            title="ANTI-SPAM TRIGGERED",
            description=f"Spam detected from member {author.mention} in {message.channel.mention}.",
            fields=[
                ("User", f"{author} ({author.id})", True),
                ("Channel", message.channel.mention, True),
                ("Detection Type", trigger_type, True),
                ("Action Taken", result_str, True)
            ],
            color=discord.Color.orange()
        )

        await asyncio.sleep(10)
        self.cooldowns.discard(cd_key)

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiSpamCog(bot))

