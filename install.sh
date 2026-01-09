#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== h-bot Install ==="

# Create .env from template if it doesn't exist
if [ ! -f .env ]; then
    cp .env.template .env
    echo "[*] Created .env from template — edit it with your tokens before starting."
    echo "    nano $SCRIPT_DIR/.env"
    echo ""
fi

# Ensure ssh-keys directory exists
mkdir -p ssh-keys

# Ensure log directories exist
mkdir -p logs/core logs/telegram

# Build and start
echo "[*] Building containers..."
docker compose build

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your tokens:  nano $SCRIPT_DIR/.env"
echo "  2. Drop SSH keys in:            $SCRIPT_DIR/ssh-keys/"
echo "  3. Start services:              docker compose up -d"
echo ""
