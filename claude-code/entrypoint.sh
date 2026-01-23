#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hcli}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/claude"
mkdir -p "$LOG_DIR/firewall"
mkdir -p "$LOG_DIR/sessions"

# Symlink sessions into /app so Claude Code's sandbox can access chunks
ln -sfn "$LOG_DIR/sessions" /app/sessions

echo "[entrypoint] Starting h-cli-claude dispatcher..."
exec "$@"
