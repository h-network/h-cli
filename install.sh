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

# Generate HMAC key for result signing if not set
if ! grep -q '^RESULT_HMAC_KEY=.\+' .env 2>/dev/null; then
    HMAC_KEY=$(openssl rand -hex 32)
    if grep -q '^RESULT_HMAC_KEY=' .env 2>/dev/null; then
        sed -i "s/^RESULT_HMAC_KEY=.*/RESULT_HMAC_KEY=$HMAC_KEY/" .env
    else
        echo "RESULT_HMAC_KEY=$HMAC_KEY" >> .env
    fi
    echo "[*] Generated HMAC key for result signing."
    echo ""
fi

# Generate Qdrant API key if not set
if ! grep -q '^QDRANT_API_KEY=.\+' .env 2>/dev/null; then
    QDRANT_KEY=$(openssl rand -hex 32)
    if grep -q '^QDRANT_API_KEY=' .env 2>/dev/null; then
        sed -i "s/^QDRANT_API_KEY=.*/QDRANT_API_KEY=$QDRANT_KEY/" .env
    else
        echo "QDRANT_API_KEY=$QDRANT_KEY" >> .env
    fi
    echo "[*] Generated Qdrant API key."
    echo ""
fi

# Generate TimescaleDB password and connection URL if not set
if ! grep -q '^TIMESCALE_PASSWORD=.\+' .env 2>/dev/null; then
    TS_PASS=$(openssl rand -hex 32)
    if grep -q '^TIMESCALE_PASSWORD=' .env 2>/dev/null; then
        sed -i "s/^TIMESCALE_PASSWORD=.*/TIMESCALE_PASSWORD=$TS_PASS/" .env
    elif grep -q '^#TIMESCALE_PASSWORD=' .env 2>/dev/null; then
        sed -i "s/^#TIMESCALE_PASSWORD=.*/TIMESCALE_PASSWORD=$TS_PASS/" .env
    else
        echo "TIMESCALE_PASSWORD=$TS_PASS" >> .env
    fi
    # Also set TIMESCALE_URL with the generated password
    TS_URL="postgresql://hcli:${TS_PASS}@h-cli-timescaledb:5432/hcli_metrics"
    if grep -q '^#\?TIMESCALE_URL=' .env 2>/dev/null; then
        sed -i "s|^#\?TIMESCALE_URL=.*|TIMESCALE_URL=$TS_URL|" .env
    else
        echo "TIMESCALE_URL=$TS_URL" >> .env
    fi
    echo "[*] Generated TimescaleDB password and connection URL."
    echo ""
fi

# Generate Grafana admin password if not set
if ! grep -q '^GRAFANA_ADMIN_PASSWORD=.\+' .env 2>/dev/null; then
    GF_PASS=$(openssl rand -hex 32)
    if grep -q '^GRAFANA_ADMIN_PASSWORD=' .env 2>/dev/null; then
        sed -i "s/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$GF_PASS/" .env
    elif grep -q '^#GRAFANA_ADMIN_PASSWORD=' .env 2>/dev/null; then
        sed -i "s/^#GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$GF_PASS/" .env
    else
        echo "GRAFANA_ADMIN_PASSWORD=$GF_PASS" >> .env
    fi
    echo "[*] Generated Grafana admin password."
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
mkdir -p logs/core logs/telegram logs/sessions logs/claude logs/firewall logs/memory
chown -R 1000:1000 logs/claude logs/firewall logs/sessions logs/telegram logs/memory

# Persistent data directories (uid 1000 = hcli user in containers)
mkdir -p -m 700 data/redis data/claude-credentials data/qdrant data/timescaledb data/grafana
chown -R 1000:1000 data/redis data/claude-credentials data/qdrant
# TimescaleDB runs as postgres (uid 70 in alpine, 999 in debian — let container own it)
# Grafana runs as grafana (uid 472)
chown -R 472:472 data/grafana

# Shortcut to Claude Code conversation JSONL files
mkdir -p data/claude-credentials/projects/-app
ln -sfn claude-credentials/projects/-app data/conversations

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
