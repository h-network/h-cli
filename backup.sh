#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${1:-$SCRIPT_DIR/backups}"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)

mkdir -p "$BACKUP_DIR"
tar czf "$BACKUP_DIR/h-cli-backup-${TIMESTAMP}.tar.gz" \
    -C "$SCRIPT_DIR" data/ logs/ .env
echo "Backup: $BACKUP_DIR/h-cli-backup-${TIMESTAMP}.tar.gz"
