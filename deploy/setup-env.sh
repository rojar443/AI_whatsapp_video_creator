#!/usr/bin/env bash
# Builds a correct production .env: generates the secrets, asks for the bot token, AI key and server address.
# Run on the server, from anywhere:  bash deploy/setup-env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.production.example ] || { echo "Run this from the project (.env.production.example not found)."; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required (sudo apt install -y openssl)"; exit 1; }

# Ask and validate everything before touching .env, so a typo never breaks a working setup.
read -r -s -p "Telegram bot token (from @BotFather, input hidden): " TOKEN; echo
read -r -p "Bot username without @ [Chat2ReelBot]: " BOT; BOT=${BOT:-Chat2ReelBot}
read -r -s -p "AI API key (Pollinations, input hidden): " AIKEY; echo
[[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { echo "That doesn't look like a Telegram bot token (expected 123456789:AA...). Run again; .env was not changed."; exit 1; }
[ -n "$AIKEY" ] || { echo "The AI key is empty. Run again; .env was not changed."; exit 1; }

# value of KEY in the existing .env, empty if missing
oldvar() { [ -f .env ] && grep "^$1=" .env | tail -n1 | cut -d= -f2- || true; }

# The server's address: defaults to the existing .env, else this server's detected public IP.
IP_DEFAULT=$(oldvar SITE_IP)
[ -n "$IP_DEFAULT" ] || IP_DEFAULT=$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)
read -r -p "Server public IP [${IP_DEFAULT:-none detected}]: " IP; IP=${IP:-$IP_DEFAULT}
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "That doesn't look like an IPv4 address. Run again; .env was not changed."; exit 1; }
ADDR_DEFAULT=$(oldvar SITE_ADDRESS)
{ [ -n "$ADDR_DEFAULT" ] && [ "$(oldvar SITE_IP)" = "$IP" ]; } || ADDR_DEFAULT="${IP//./-}.sslip.io"
read -r -p "Domain for HTTPS (must point at this server) [$ADDR_DEFAULT]: " ADDR; ADDR=${ADDR:-$ADDR_DEFAULT}
[[ "$ADDR" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "That doesn't look like a hostname (no https://, no path). Run again; .env was not changed."; exit 1; }

# Build the new file aside and swap it in only at the end.
NEW=$(mktemp .env.new.XXXXXX)
trap 'rm -f "$NEW" "$NEW.tmp"' EXIT
cp .env.production.example "$NEW"

# set KEY=value without sed, so tokens with any characters are safe
setvar() { grep -v "^$1=" "$NEW" > "$NEW.tmp" || true; printf '%s=%s\n' "$1" "$2" >> "$NEW.tmp"; mv "$NEW.tmp" "$NEW"; }
# Keep secrets from an existing .env: the postgres volume keeps its first password,
# and a new SECRET_KEY would log everyone out.
for k in SECRET_KEY POSTGRES_PASSWORD; do
  v=$(oldvar "$k"); [ -n "$v" ] && setvar "$k" "$v"
done
[ -n "$(oldvar SECRET_KEY)" ] || setvar SECRET_KEY "$(openssl rand -hex 32)"
[ -n "$(oldvar POSTGRES_PASSWORD)" ] || setvar POSTGRES_PASSWORD "$(openssl rand -hex 16)"

setvar SITE_ADDRESS "$ADDR"
setvar SITE_IP "$IP"
setvar WEB_BASE_URL "https://$ADDR"
setvar PUBLIC_BASE_URL "https://$ADDR"
setvar CORS_ORIGINS "https://$ADDR,https://$IP"

setvar TELEGRAM_BOT_TOKEN "$TOKEN"
setvar TELEGRAM_BOT_USERNAME "$BOT"
setvar OPENAI_API_KEY "$AIKEY"
chmod 600 "$NEW"

if [ -f .env ]; then
  cp .env ".env.bak.$(date +%s)"
  echo "Existing .env backed up to .env.bak.* (secrets kept)"
fi
mv "$NEW" .env

echo; echo "Done. .env created with these values set (secrets not shown):"
for k in SITE_ADDRESS SITE_IP WEB_BASE_URL CORS_ORIGINS SECRET_KEY POSTGRES_PASSWORD TELEGRAM_BOT_TOKEN TELEGRAM_BOT_USERNAME OPENAI_API_KEY AI_MODEL; do
  v=$(grep "^$k=" .env | cut -d= -f2-)
  case $k in SECRET_KEY|POSTGRES_PASSWORD|TELEGRAM_BOT_TOKEN|OPENAI_API_KEY) printf '  %-22s set (%d characters)\n' "$k" "${#v}";;
                                                                         *) printf '  %-22s %s\n' "$k" "$v";; esac
done
if ! command -v docker >/dev/null; then
  echo; echo "Config not checked: docker is not installed yet; install it first."
else
  ERR=$(mktemp)
  if docker compose -f docker-compose.prod.yml config --quiet 2>"$ERR"; then
    echo; echo "Config check passed. Start with:"; echo "  docker compose -f docker-compose.prod.yml up -d --build"
  else
    echo; echo "Config check did not pass:"; cat "$ERR"
  fi
  rm -f "$ERR"
fi
