#!/usr/bin/env zsh
# ============================================================================
# log4AI - ZSH Shell Command Logger
# ============================================================================
# Logs every command + output as structured JSON to ~/.log4AI/
#
# Install:
#   mkdir -p ~/.log4AI
#   cp log4ai.zsh ~/.log4AI/log4ai.zsh
#   echo 'source ~/.log4AI/log4ai.zsh' >> ~/.zshrc
#   source ~/.zshrc
#
# Uninstall:
#   Remove the source line from ~/.zshrc
# ============================================================================

LOG4AI_DIR="${HOME}/.log4AI"
LOG4AI_SESSION_ID=$(date +%s)-$
LOG4AI_ENABLED=true

# Ensure log directory exists
mkdir -p "${LOG4AI_DIR}"

# Commands to skip logging (passwords, secrets, sensitive stuff)
LOG4AI_BLACKLIST=(
  "pass" "gpg" "ssh-keygen" "ssh-add"
  "export.*KEY" "export.*SECRET" "export.*TOKEN" "export.*PASS"
  "vault" "aws configure" "kubectl exec"
)

# Max output capture in bytes (default 64KB, keeps logs sane)
LOG4AI_MAX_OUTPUT=${LOG4AI_MAX_OUTPUT:-65536}

# --------------------------------------------------------------------------
# Helper: JSON-escape a string
# --------------------------------------------------------------------------
_log4ai_json_escape() {
  local input="$1"
  # Truncate if needed
  if [[ ${#input} -gt ${LOG4AI_MAX_OUTPUT} ]]; then
    input="${input:0:${LOG4AI_MAX_OUTPUT}}...[TRUNCATED]"
  fi
  # Escape backslashes, quotes, newlines, tabs, carriage returns
  printf '%s' "$input" | python3 -c '
import sys, json
raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
print(json.dumps(raw), end="")
' 2>/dev/null || printf '"%s"' "error_encoding"
}

# --------------------------------------------------------------------------
# Helper: Check if command is blacklisted
# --------------------------------------------------------------------------
_log4ai_is_blacklisted() {
  local cmd="$1"
  for pattern in "${LOG4AI_BLACKLIST[@]}"; do
    if [[ "$cmd" =~ ${pattern} ]]; then
      return 0
    fi
  done
  return 1
}

# --------------------------------------------------------------------------
# Helper: Get today's log file (one JSONL file per day)
# --------------------------------------------------------------------------
_log4ai_logfile() {
  echo "${LOG4AI_DIR}/$(date +%Y-%m-%d).jsonl"
}

# --------------------------------------------------------------------------
# preexec: Fires BEFORE a command executes
# --------------------------------------------------------------------------
_log4ai_preexec() {
  [[ "${LOG4AI_ENABLED}" != "true" ]] && return

  local cmd="$1"

  # Skip blacklisted commands
  if _log4ai_is_blacklisted "$cmd"; then
    _LOG4AI_SKIP=true
    return
  fi

  _LOG4AI_SKIP=false
  _LOG4AI_CMD="$cmd"
  _LOG4AI_START_MS=$(($(date +%s%N 2>/dev/null || echo $(date +%s)000000000) / 1000000))
  _LOG4AI_CWD="$(pwd)"

  # Capture output via temp file
  _LOG4AI_OUTFILE=$(mktemp "${TMPDIR:-/tmp}/log4ai.XXXXXX")

  # Start script capture (unbuffered, quiet)
  exec > >(tee -a "${_LOG4AI_OUTFILE}") 2>&1
}

# --------------------------------------------------------------------------
# precmd: Fires AFTER a command completes, BEFORE next prompt
# --------------------------------------------------------------------------
_log4ai_precmd() {
  local exit_code=$?

  [[ "${LOG4AI_ENABLED}" != "true" ]] && return
  [[ "${_LOG4AI_SKIP}" == "true" ]] && return
  [[ -z "${_LOG4AI_CMD}" ]] && return

  # Stop output capture - restore normal stdout/stderr
  exec > /dev/tty 2>&1

  local end_ms=$(($(date +%s%N 2>/dev/null || echo $(date +%s)000000000) / 1000000))
  local duration_ms=$(( end_ms - _LOG4AI_START_MS ))
  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local hostname=$(hostname -s)

  # Read captured output
  local output=""
  if [[ -f "${_LOG4AI_OUTFILE}" ]]; then
    output=$(cat "${_LOG4AI_OUTFILE}")
    rm -f "${_LOG4AI_OUTFILE}"
  fi

  # Build JSON log entry
  local json_cmd=$(_log4ai_json_escape "${_LOG4AI_CMD}")
  local json_output=$(_log4ai_json_escape "${output}")
  local json_cwd=$(_log4ai_json_escape "${_LOG4AI_CWD}")

  local logentry=$(cat <<EOF
{"timestamp":"${timestamp}","host":"${hostname}","session":"${LOG4AI_SESSION_ID}","cwd":${json_cwd},"command":${json_cmd},"exit_code":${exit_code},"duration_ms":${duration_ms},"output":${json_output},"shell":"zsh"}
EOF
)

  # Append to today's JSONL log
  echo "${logentry}" >> "$(_log4ai_logfile)"

  # Cleanup
  unset _LOG4AI_CMD _LOG4AI_START_MS _LOG4AI_CWD _LOG4AI_OUTFILE
}

# --------------------------------------------------------------------------
# Register hooks
# --------------------------------------------------------------------------
autoload -Uz add-zsh-hook
add-zsh-hook preexec _log4ai_preexec
add-zsh-hook precmd _log4ai_precmd

# --------------------------------------------------------------------------
# Utility commands
# --------------------------------------------------------------------------

# Toggle logging on/off
log4ai() {
  case "$1" in
    on)
      LOG4AI_ENABLED=true
      echo "[log4AI] Logging enabled"
      ;;
    off)
      LOG4AI_ENABLED=false
      echo "[log4AI] Logging disabled"
      ;;
    status)
      echo "[log4AI] Enabled: ${LOG4AI_ENABLED}"
      echo "[log4AI] Session: ${LOG4AI_SESSION_ID}"
      echo "[log4AI] Log dir: ${LOG4AI_DIR}"
      echo "[log4AI] Today's log: $(_log4ai_logfile)"
      if [[ -f "$(_log4ai_logfile)" ]]; then
        local count=$(wc -l < "$(_log4ai_logfile)")
        echo "[log4AI] Commands logged today: ${count}"
      fi
      ;;
    tail)
      local n=${2:-10}
      tail -n "${n}" "$(_log4ai_logfile)" | python3 -m json.tool 2>/dev/null || tail -n "${n}" "$(_log4ai_logfile)"
      ;;
    stats)
      echo "[log4AI] Log files:"
      ls -lh "${LOG4AI_DIR}"/*.jsonl 2>/dev/null || echo "  No logs yet"
      echo ""
      echo "[log4AI] Total commands logged:"
      cat "${LOG4AI_DIR}"/*.jsonl 2>/dev/null | wc -l
      ;;
    *)
      echo "Usage: log4ai {on|off|status|tail [n]|stats}"
      ;;
  esac
}

echo "[log4AI] ZSH logger active | session: ${LOG4AI_SESSION_ID} | log4ai status for info"
