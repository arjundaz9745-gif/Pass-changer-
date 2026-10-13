# MS Password Changer Bot

Discord bot for Microsoft account recovery (ACSR) automation.

## Requirements

- Python **3.10+** (3.11–3.13 recommended; 3.14 may work)
- Chrome / Chromium available on the host (Selenium)
- Discord bot token

Install:

```bash
pip install -r requirements.txt
```

## Project layout

```text
.
├── main.py                 # bot entry
├── requirements.txt
├── tempmail.py             # temp-mail helpers
├── automation/
│   ├── core.py             # login + scrape
│   ├── acsr.py
│   ├── acsr_continue.py
│   ├── reset_password.py
│   ├── captcha.py
│   ├── driver.py
│   └── ...
├── emojis.txt              # custom emoji names to add on server
└── README.md
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | **Yes** | Bot token (or `DISCORD_BOT_TOKEN`) |
| `PORT` | No | HTTP health port (Render sets this; default `10000`) |
| `GUILD_ID` | No | One server ID, or several: `id1,id2,id3` |

On **Render**: set `DISCORD_TOKEN`, start command:

```bash
python main.py
```

## Discord setup

1. [Discord Developer Portal](https://discord.com/developers/applications) → your app → **Bot**
2. Enable **Server Members Intent** (role checks)
3. **Interactions Endpoint URL** → leave **empty** (gateway bot only)
4. Invite with scopes: `bot` + `applications.commands`
5. Permissions: Send Messages, Embed Links, Attach Files, Use Slash Commands, Read Message History

## Who can use the bot

- **Permanent admins** (no OTP needed):  
  `1541659394188509215`, `1398979148063571989`, `1550540531665473699`
- **Role:** `1558345972516392970`
- Or users granted with `/authorize @user` (admin)

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Help |
| `/request_otp` | OTP in DM (admins auto-login) |
| `/verify_otp <code>` | Verify OTP |
| `/logout` | End session |
| `/process email:password` | Start recovery |
| `/submit_captcha <text>` | Submit CAPTCHA |
| `/status` | Session status |
| `/cancel` | Cancel process |
| `/admin` | Admin panel |
| `/authorize @user` | Grant access |
| `/revoke @user` | Remove access |
| `/list_users` | List authorized |
| `/set_webhook <url>` | Results webhook |
| `/stats` | Stats |

## How to use (staff)

1. `/request_otp` → check DMs (admins skip)
2. `/verify_otp 123456`
3. `/process user@outlook.com:TheirPassword`
4. Bot posts CAPTCHA image → `/submit_captcha ab12cd`
5. Bot finishes ACSR and posts the new password

## Custom emojis

See **`emojis.txt`**. Upload each name as a custom emoji on the server (animated allowed).  
The bot looks them up by **name**; if missing, it falls back to plain text.

## Local run

```bash
export DISCORD_TOKEN="your_token"
python main.py
```

## Notes

- Selenium needs Chrome/Chromium on the machine (Render free tier often lacks a working browser — use a VPS or Docker with Chrome if automation fails).
- If slash commands say **Application did not respond**, the bot is not connected to the Discord gateway yet. Check logs for `ONLINE` and a successful token pre-check.
- Never commit your real bot token.

## License

Private / internal use.
