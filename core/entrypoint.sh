#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hbot}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/core"

SSH_STAGING="/tmp/ssh-keys-staging"
SSH_DIR="/root/.ssh"

# Set up SSH keys if staging directory has files
if [ -d "$SSH_STAGING" ] && [ "$(ls -A $SSH_STAGING 2>/dev/null)" ]; then
    echo "[entrypoint] Setting up SSH keys..."

    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"

    # Copy all files from staging (read-only mount) to writable location
    cp -a "$SSH_STAGING"/. "$SSH_DIR"/

    # Re-apply directory permission (cp -a may override it)
    chmod 700 "$SSH_DIR"

    # Remove leftover git artifacts first
    rm -f "$SSH_DIR/.gitkeep" "$SSH_DIR/README.md"

    # Fix permissions on private keys (no .pub, no config, no known_hosts)
    for f in "$SSH_DIR"/*; do
        [ -e "$f" ] || continue
        filename=$(basename "$f")
        case "$filename" in
            *.pub)
                chmod 644 "$f"
                ;;
            config|known_hosts|authorized_keys)
                chmod 644 "$f"
                ;;
            *)
                chmod 600 "$f"
                ;;
        esac
    done

    # Create default SSH config if not provided
    if [ ! -f "$SSH_DIR/config" ]; then
        cat > "$SSH_DIR/config" <<'EOF'
Host *
    StrictHostKeyChecking accept-new
    UserKnownHostsFile /root/.ssh/known_hosts
    ServerAliveInterval 60
    ServerAliveCountMax 3
EOF
        chmod 644 "$SSH_DIR/config"
    fi

    echo "[entrypoint] SSH keys configured:"
    ls -la "$SSH_DIR"/ | grep -v "^total"
else
    echo "[entrypoint] No SSH keys found in $SSH_STAGING, skipping SSH setup."
fi

echo "[entrypoint] Starting h-bot-core..."
exec "$@"
