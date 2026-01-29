#!/bin/bash
set -e

LOG_DIR="${LOG_DIR:-/var/log/hcli}"

echo "[entrypoint] Creating log directories..."
mkdir -p "$LOG_DIR/core"

SSH_STAGING="/tmp/ssh-keys-staging"
SSH_DIR="/home/hcli/.ssh"

# Set up SSH keys if staging directory has files
if [ -d "$SSH_STAGING" ] && [ "$(ls -A "$SSH_STAGING" 2>/dev/null)" ]; then
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
    UserKnownHostsFile /home/hcli/.ssh/known_hosts
    ServerAliveInterval 60
    ServerAliveCountMax 3
EOF
        chmod 644 "$SSH_DIR/config"
    fi

    chown -R hcli:hcli "$SSH_DIR"

    echo "[entrypoint] SSH keys configured:"
    ls -la "$SSH_DIR"/ | grep -v "^total"
else
    echo "[entrypoint] No SSH keys found in $SSH_STAGING, skipping SSH setup."
fi

chown -R hcli:hcli "$LOG_DIR/core"

# ── Sudo whitelist ──────────────────────────────────────────────────
# Argument restrictions per command to prevent privilege escalation.
# ip: deny "netns exec" (root shell), allow everything else
# nmap: deny "--script" and "--interactive" (arbitrary code exec)
# iptables: deny "--modprobe" (arbitrary command as root)
# tcpdump: deny "-z" (post-rotation command as root)
# All others: unrestricted (read-only tools like traceroute, mtr, ping, ss)
if [ -n "${SUDO_COMMANDS:-}" ]; then
    echo "[entrypoint] Configuring sudo whitelist..."

    # Wrapper script approach: instead of fighting sudoers argument syntax,
    # create wrapper scripts that validate arguments before execution.
    # Sudoers whitelists only the wrappers, not the raw binaries.
    WRAPPER_DIR="/usr/local/bin"
    SUDOERS_LINE="hcli ALL=(ALL) NOPASSWD:"
    FIRST=true

    IFS=',' read -ra CMDS <<< "$SUDO_COMMANDS"
    for cmd in "${CMDS[@]}"; do
        cmd=$(echo "$cmd" | xargs)  # trim whitespace
        FULL=$(command -v "$cmd" 2>/dev/null || true)
        if [ -z "$FULL" ]; then
            echo "[entrypoint] WARNING: command '$cmd' not found, skipping"
            continue
        fi

        WRAPPER="$WRAPPER_DIR/hcli-$cmd"

        case "$cmd" in
            ip)
                cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
# Block: ip netns exec (root shell escape)
for arg in "\$@"; do
    if [ "\$prev" = "netns" ] && [ "\$arg" = "exec" ]; then
        echo "DENIED: 'ip netns exec' is blocked (privilege escalation)" >&2
        exit 1
    fi
    prev="\$arg"
done
exec $FULL "\$@"
WRAPPER_EOF
                ;;
            nmap)
                cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
# Block: --script (Lua code exec), --interactive
for arg in "\$@"; do
    case "\$arg" in
        --script|--script=*|--interactive)
            echo "DENIED: '\$arg' is blocked (arbitrary code execution)" >&2
            exit 1
            ;;
    esac
done
exec $FULL "\$@"
WRAPPER_EOF
                ;;
            iptables)
                cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
# Block: --modprobe (arbitrary command as root)
for arg in "\$@"; do
    case "\$arg" in
        --modprobe|--modprobe=*)
            echo "DENIED: '\$arg' is blocked (arbitrary command execution)" >&2
            exit 1
            ;;
    esac
done
exec $FULL "\$@"
WRAPPER_EOF
                ;;
            tcpdump)
                cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
# Block: -z (post-rotation command as root)
for arg in "\$@"; do
    case "\$arg" in
        -z)
            echo "DENIED: '-z' is blocked (arbitrary command execution)" >&2
            exit 1
            ;;
    esac
done
exec $FULL "\$@"
WRAPPER_EOF
                ;;
            *)
                # Safe read-only tools: traceroute, mtr, ping, ss — no wrapper needed
                cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
exec $FULL "\$@"
WRAPPER_EOF
                ;;
        esac

        chmod 755 "$WRAPPER"
        if $FIRST; then
            SUDOERS_LINE="$SUDOERS_LINE $WRAPPER"
            FIRST=false
        else
            SUDOERS_LINE="$SUDOERS_LINE, $WRAPPER"
        fi
    done

    if ! $FIRST; then
        echo "$SUDOERS_LINE" > /etc/sudoers.d/hcli
        chmod 440 /etc/sudoers.d/hcli
        echo "[entrypoint] Sudo whitelist configured via wrappers with argument restrictions:"
        cat /etc/sudoers.d/hcli
    else
        echo "[entrypoint] WARNING: No valid commands found, sudo disabled"
        rm -f /etc/sudoers.d/hcli
    fi
else
    echo "[entrypoint] No SUDO_COMMANDS set, sudo disabled for hcli"
    rm -f /etc/sudoers.d/hcli
fi

echo "[entrypoint] Starting h-cli-core as hcli..."
exec gosu hcli "$@"
