#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== h-cli Install ==="

# Create .env from template if it doesn't exist
if [ ! -f .env ]; then
    cp .env.template .env
    echo "[*] Created .env from template — edit it with your tokens before starting."
    echo "    nano $SCRIPT_DIR/.env"
    echo ""
fi

# Ensure ssh-keys directory exists
mkdir -p -m 700 ssh-keys

# Generate dedicated SSH keypair if none exists
if ! ls ssh-keys/id_* &>/dev/null; then
    echo "[*] Generating hcli SSH keypair..."
    ssh-keygen -t ed25519 -f ssh-keys/id_ed25519 -N "" -C "hcli@$(hostname)"
    echo ""
    echo "[*] Public key (add this to your servers' authorized_keys):"
    echo ""
    cat ssh-keys/id_ed25519.pub
    echo ""
fi

# Create context.md from template if it doesn't exist
if [ ! -f context.md ]; then
    cp context.md.template context.md
    echo "[*] Created context.md from template — customize it to describe your deployment."
    echo "    nano $SCRIPT_DIR/context.md"
    echo ""
fi

# Ensure log directories exist (uid 1000 = hcli user in claude-code container)
mkdir -p logs/core logs/telegram logs/sessions logs/claude logs/firewall
chown -R 1000:1000 logs/claude logs/firewall logs/sessions

# Build and start
echo "[*] Building containers..."
docker compose build

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your tokens:  nano $SCRIPT_DIR/.env"
echo "  2. Add the public key to your servers:  ssh-copy-id -i $SCRIPT_DIR/ssh-keys/id_ed25519.pub user@host"
echo "  3. Start services:              docker compose up -d"
echo ""
