#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hbot}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/telegram"

echo "[entrypoint] Starting h-bot-telegram..."
exec "$@"
