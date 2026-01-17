# SSH Keys for h-bot-core

Drop your SSH private keys into this directory. They will be mounted read-only into the h-bot-core container and copied to `/home/hbot/.ssh/` with correct permissions on startup.

## Supported Files

| File | Purpose |
|------|---------|
| `id_rsa`, `id_ed25519`, etc. | Private keys |
| `*.pub` | Public keys (optional) |
| `config` | SSH client config (optional) |
| `known_hosts` | Pre-approved host keys (optional) |

## Usage

```bash
# Copy a key
cp ~/.ssh/id_ed25519 ./ssh-keys/

# Optionally add SSH config
cp ~/.ssh/config ./ssh-keys/

# Restart core to pick up new keys
docker compose restart h-bot-core
```

## Security

- This directory is excluded from git tracking via `.gitignore`
- Keys are mounted read-only into the container
- The entrypoint copies and fixes permissions internally
- Never commit private keys to version control
