#!/bin/bash
set -e

# Bootstrap a dev instance of h-cli at ~/h-cli-dev
# Run this on h-srv from the prod repo: bash setup-dev.sh

REPO_URL="git@git.hb-l.nl:halil/h-cli.git"
DEV_DIR="$HOME/h-cli-dev"

echo "=== h-cli Dev Environment Setup ==="
echo ""

# Clone repo if not present
if [ -d "$DEV_DIR" ]; then
    echo "[*] $DEV_DIR already exists — pulling latest..."
    git -C "$DEV_DIR" pull
else
    echo "[*] Cloning repo to $DEV_DIR..."
    git clone "$REPO_URL" "$DEV_DIR"
fi

cd "$DEV_DIR"

# Create .env from template
cp .env.template .env
echo "[*] Created .env from template."

# Set ENV_TAG for dev
sed -i 's/^ENV_TAG=.*/ENV_TAG=dev-/' .env
echo "[*] Set ENV_TAG=dev-"

# Generate fresh REDIS_PASSWORD
REDIS_PASS=$(openssl rand -hex 24)
sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$REDIS_PASS/" .env
echo "[*] Generated REDIS_PASSWORD."

# Generate fresh RESULT_HMAC_KEY
HMAC_KEY=$(openssl rand -hex 32)
sed -i "s/^RESULT_HMAC_KEY=.*/RESULT_HMAC_KEY=$HMAC_KEY/" .env
echo "[*] Generated RESULT_HMAC_KEY."

# Prompt for dev bot token and chat ID
echo ""
read -rp "Dev Telegram bot token: " DEV_BOT_TOKEN
read -rp "Dev allowed chat IDs (comma-separated): " DEV_CHAT_IDS

sed -i "s/^TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=$DEV_BOT_TOKEN/" .env
sed -i "s/^ALLOWED_CHATS=.*/ALLOWED_CHATS=$DEV_CHAT_IDS/" .env
echo "[*] Configured Telegram settings."

# Disable gate check by default in dev (faster iteration)
sed -i 's/^GATE_CHECK=.*/GATE_CHECK=false/' .env
echo "[*] Disabled GATE_CHECK for dev (set to true in .env to enable)."

# Run install.sh (creates ssh-keys, log dirs, builds containers)
echo ""
echo "[*] Running install.sh..."
bash install.sh

echo ""
echo "=== Dev Environment Ready ==="
echo ""
echo "Next steps:"
echo "  1. Log in to Claude Code:"
echo "     cd $DEV_DIR"
echo "     docker compose run -it --entrypoint bash claude-code"
echo "     cd ~ && claude"
echo "     # Complete login wizard, then exit"
echo ""
echo "  2. Start dev stack:"
echo "     cd $DEV_DIR && docker compose up -d"
echo ""
echo "  3. Verify:"
echo "     docker ps | grep h-cli-dev"
echo "     docker logs h-cli-dev-telegram"
echo ""
echo "  4. Tear down:"
echo "     cd $DEV_DIR && docker compose down"
echo ""
