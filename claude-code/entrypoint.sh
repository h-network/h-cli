#!/bin/bash
set -e

echo "[entrypoint] Starting h-cli-claude dispatcher as $(whoami)..."
exec "$@"
