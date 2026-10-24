#!/usr/bin/env bash
# Builds a correct production .env: generates the secrets, asks only for the bot token and AI key.
# Run on the server, from anywhere:  bash deploy/setup-env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.production.example ] || { echo "Run this from the project (.env.production.example not found)."; exit 1; }
if [ -f .env ]; then
  cp .env ".env.bak.$(date +%s)"
  echo "Existing .env backed up to .env.bak.*"
fi
cp .env.production.example .env

# set KEY=value without sed, so tokens with any characters are safe
setvar() { grep -v "^$1=" .env > .env.tmp || true; printf '%s=%s\n' "$1" "$2" >> .env.tmp; mv .env.tmp .env; }

command -v openssl >/dev/null || { echo "openssl is required (sudo apt install -y openssl)"; exit 1; }
setvar SECRET_KEY "$(openssl rand -hex 32)"
setvar POSTGRES_PASSWORD "$(openssl rand -hex 16)"

read -r -s -p "Telegram bot token (from @BotFather, input hidden): " TOKEN; echo
read -r -p "Bot username without @ [Chat2ReelBot]: " BOT; BOT=${BOT:-Chat2ReelBot}
read -r -s -p "AI API key (Pollinations, input hidden): " AIKEY; echo

[[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { echo "That doesn't look like a Telegram bot token (expected 123456789:AA...). Run again."; rm -f .env; exit 1; }
[ -n "$AIKEY" ] || { echo "The AI key is empty. Run again."; rm -f .env; exit 1; }
setvar TELEGRAM_BOT_TOKEN "$TOKEN"
setvar TELEGRAM_BOT_USERNAME "$BOT"
setvar OPENAI_API_KEY "$AIKEY"
chmod 600 .env

echo; echo "Done. .env created with these values set (secrets not shown):"
for k in SITE_ADDRESS SITE_IP SECRET_KEY POSTGRES_PASSWORD TELEGRAM_BOT_TOKEN TELEGRAM_BOT_USERNAME OPENAI_API_KEY AI_MODEL; do
  v=$(grep "^$k=" .env | cut -d= -f2-)
  case $k in SECRET_KEY|POSTGRES_PASSWORD|TELEGRAM_BOT_TOKEN|OPENAI_API_KEY) printf '  %-22s set (%d characters)\n' "$k" "${#v}";;
                                                                         *) printf '  %-22s %s\n' "$k" "$v";; esac
done
if command -v docker >/dev/null && docker compose -f docker-compose.prod.yml config --quiet 2>/tmp/compose-err; then
  echo; echo "Config check passed. Start with:"; echo "  docker compose -f docker-compose.prod.yml up -d --build"
else
  echo; echo "Config check did not pass:"; cat /tmp/compose-err 2>/dev/null || echo "(docker not installed yet; install it first)"
fi
