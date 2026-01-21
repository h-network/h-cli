#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hcli}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/claude"
mkdir -p "$LOG_DIR/sessions"

echo "[entrypoint] Starting h-cli-claude dispatcher..."
exec "$@"
