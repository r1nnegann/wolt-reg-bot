# Security

## Secrets

This repository must never contain:

- Telegram bot tokens
- HeroSMS API keys
- webhook path tokens
- `.env`, SQLite databases, Mail.tm passwords, SMS codes

Copy `.env.example` → `.env` locally (`chmod 600 .env`). Rotate any credential that has appeared in a chat, screenshot, journal, or git history.

## Reporting

If you find a secret in this repo or a way to read another user's activations, open a private report: GitHub → **Security** → **Advisories** → **New draft security advisory**. Do not paste live tokens into issues.

## Runtime rules baked into the bot

- HeroSMS `api_key` goes in the query string of the legacy API. `httpx`/`httpcore` loggers are forced to WARNING so request URLs do not land in journald.
- Changing the admin HeroSMS key deletes the Telegram message with the secret and logs only a mask + balance.
- The webhook listener binds to loopback by default and answers `404` on a wrong token.
- Duplicate HeroSMS deliveries are claimed atomically in SQLite; a replay does not send a second Telegram message.
