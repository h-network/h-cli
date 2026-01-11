#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hbot}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/claude"

echo "[entrypoint] Starting h-bot-claude dispatcher..."
exec "$@"
