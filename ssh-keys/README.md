# SSH Keys for h-cli-core

On first install, `install.sh` auto-generates a dedicated **ed25519** keypair (`id_ed25519` / `id_ed25519.pub`) with the comment `hcli@<hostname>`. The public key is printed to stdout so you can copy it to your servers.

If you prefer to use your own keys, drop them here before running `install.sh` — generation is skipped when any `id_*` file already exists.

Keys are mounted read-only into the h-cli-core container and copied to `/home/hcli/.ssh/` with correct permissions on startup.

## Supported Files

| File | Purpose |
|------|---------|
| `id_ed25519` | Auto-generated private key |
| `id_ed25519.pub` | Auto-generated public key |
| `id_rsa`, other `id_*` | User-provided private keys |
| `*.pub` | Public keys (optional) |
| `config` | SSH client config (optional) |
| `known_hosts` | Pre-approved host keys (optional) |

## Usage

```bash
# Auto-generated key — copy public key to your servers
ssh-copy-id -i ./ssh-keys/id_ed25519.pub user@your-server

# Or use your own key instead (skip auto-generation)
cp ~/.ssh/id_ed25519 ./ssh-keys/

# Optionally add SSH config
cp ~/.ssh/config ./ssh-keys/

# Restart core to pick up new/changed keys
docker compose restart h-cli-core
```

## Security

- This directory is excluded from git tracking via `.gitignore`
- Keys are mounted read-only into the container
- The entrypoint copies and fixes permissions internally
- Never commit private keys to version control
- The auto-generated key is dedicated to hcli — easy to revoke without affecting personal keys
