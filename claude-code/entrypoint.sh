#!/bin/bash
set -e

# Persist .claude.json across rebuilds — stored inside the mounted .claude/ dir,
# symlinked to home so Claude writes directly to the volume.
ln -sf /home/hcli/.claude/.claude.json /home/hcli/.claude.json

echo "[entrypoint] Starting h-cli-claude dispatcher as $(whoami)..."
exec "$@"
