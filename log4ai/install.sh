#!/usr/bin/env bash
# ============================================================================
# log4AI Installer
# ============================================================================
# Usage:
#   curl -sL <your-repo>/install.sh | bash
#   or
#   git clone <repo> && cd log4ai && ./install.sh
# ============================================================================

set -e

LOG4AI_DIR="${HOME}/.log4AI"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  log4AI - Shell Command Logger for AI"
echo "============================================"
echo ""

# Create log directory
mkdir -p "${LOG4AI_DIR}"
echo "[+] Created ${LOG4AI_DIR}"

# Detect shell
CURRENT_SHELL=$(basename "$SHELL")
echo "[+] Detected shell: ${CURRENT_SHELL}"

# Copy the appropriate script
if [[ "$CURRENT_SHELL" == "zsh" ]]; then
  if [[ -f "${SCRIPT_DIR}/log4ai.zsh" ]]; then
    cp "${SCRIPT_DIR}/log4ai.zsh" "${LOG4AI_DIR}/log4ai.zsh"
  fi

  SOURCE_LINE='source ~/.log4AI/log4ai.zsh'
  RC_FILE="${HOME}/.zshrc"

  if ! grep -qF "log4ai.zsh" "${RC_FILE}" 2>/dev/null; then
    echo "" >> "${RC_FILE}"
    echo "# log4AI - Shell command logger for AI training data" >> "${RC_FILE}"
    echo "${SOURCE_LINE}" >> "${RC_FILE}"
    echo "[+] Added source line to ${RC_FILE}"
  else
    echo "[=] Already sourced in ${RC_FILE}"
  fi

elif [[ "$CURRENT_SHELL" == "bash" ]]; then
  if [[ -f "${SCRIPT_DIR}/log4ai.bash" ]]; then
    cp "${SCRIPT_DIR}/log4ai.bash" "${LOG4AI_DIR}/log4ai.bash"
  fi

  SOURCE_LINE='source ~/.log4AI/log4ai.bash'
  RC_FILE="${HOME}/.bashrc"

  if ! grep -qF "log4ai.bash" "${RC_FILE}" 2>/dev/null; then
    echo "" >> "${RC_FILE}"
    echo "# log4AI - Shell command logger for AI training data" >> "${RC_FILE}"
    echo "${SOURCE_LINE}" >> "${RC_FILE}"
    echo "[+] Added source line to ${RC_FILE}"
  else
    echo "[=] Already sourced in ${RC_FILE}"
  fi
else
  echo "[!] Unsupported shell: ${CURRENT_SHELL}"
  echo "    Manually source log4ai.zsh or log4ai.bash from your rc file"
  exit 1
fi

# Create .gitignore for the log dir (don't accidentally commit logs)
cat > "${LOG4AI_DIR}/.gitignore" <<'EOF'
*.jsonl
EOF
echo "[+] Created .gitignore in log dir"

# Verify python3 is available (needed for JSON escaping)
if command -v python3 &>/dev/null; then
  echo "[+] python3 found: $(which python3)"
else
  echo "[!] WARNING: python3 not found - JSON escaping will fall back to basic mode"
fi

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "  Log directory:  ${LOG4AI_DIR}"
echo "  Log format:     JSONL (one JSON object per line)"
echo "  File pattern:   YYYY-MM-DD.jsonl (one per day)"
echo ""
echo "  Commands:"
echo "    log4ai status  - Show logging status"
echo "    log4ai on/off  - Toggle logging"
echo "    log4ai tail    - View recent logs (pretty)"
echo "    log4ai stats   - Show log file stats"
echo ""
echo "  Restart your shell or run:"
echo "    source ${RC_FILE}"
echo ""
