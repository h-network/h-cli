#!/usr/bin/env bash
# ============================================================================
# log4AI - BASH Shell Command Logger
# ============================================================================
# Logs every command + output as structured JSON to ~/.log4AI/
#
# Install:
#   mkdir -p ~/.log4AI
#   cp log4ai.bash ~/.log4AI/log4ai.bash
#   echo 'source ~/.log4AI/log4ai.bash' >> ~/.bashrc
#   source ~/.bashrc
#
# Uninstall:
#   Remove the source line from ~/.bashrc
# ============================================================================

LOG4AI_DIR="${HOME}/.log4AI"
LOG4AI_SESSION_ID="$(date +%s)-$"
LOG4AI_ENABLED=true
_LOG4AI_LAST_HISTNUM=""

# Ensure log directory exists
mkdir -p "${LOG4AI_DIR}"

# Commands to skip logging (passwords, secrets, sensitive stuff)
LOG4AI_BLACKLIST="pass|gpg|ssh-keygen|ssh-add|export.*KEY|export.*SECRET|export.*TOKEN|export.*PASS|vault|aws configure|kubectl exec"

# Max output capture in bytes (default 64KB)
LOG4AI_MAX_OUTPUT=${LOG4AI_MAX_OUTPUT:-65536}

# --------------------------------------------------------------------------
# Helper: JSON-escape a string (pure bash, no external processes)
# --------------------------------------------------------------------------
_log4ai_json_escape() {
  local input="$1"
  # Truncate if needed
  if [[ ${#input} -gt ${LOG4AI_MAX_OUTPUT} ]]; then
    input="${input:0:${LOG4AI_MAX_OUTPUT}}...[TRUNCATED]"
  fi
  # Escape special JSON characters using bash string replacement
  input="${input//\\/\\\\}"     # backslash first (before other escapes)
  input="${input//\"/\\\"}"     # double quotes
  input="${input//$'\n'/\\n}"   # newlines
  input="${input//$'\r'/\\r}"   # carriage returns
  input="${input//$'\t'/\\t}"   # tabs
  input="${input//$'\x08'/\\b}" # backspace
  input="${input//$'\x0c'/\\f}" # form feed
  printf '"%s"' "$input"
}

# --------------------------------------------------------------------------
# Helper: Check if command is blacklisted
# --------------------------------------------------------------------------
_log4ai_is_blacklisted() {
  local cmd="$1"
  if [[ "$cmd" =~ ${LOG4AI_BLACKLIST} ]]; then
    return 0
  fi
  return 1
}

# --------------------------------------------------------------------------
# Helper: Get today's log file
# --------------------------------------------------------------------------
_log4ai_logfile() {
  echo "${LOG4AI_DIR}/$(date +%Y-%m-%d).jsonl"
}

# --------------------------------------------------------------------------
# Bash approach: Use DEBUG trap + PROMPT_COMMAND
#
# DEBUG trap fires before each command (like zsh preexec)
# PROMPT_COMMAND fires before each prompt (like zsh precmd)
#
# Output capture in bash is trickier than zsh, so we use a wrapper
# approach: commands are executed via a function that tees output.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# DEBUG trap: Capture command before execution
# --------------------------------------------------------------------------
_log4ai_debug_trap() {
  [[ "${LOG4AI_ENABLED}" != "true" ]] && return

  # Avoid recursion from PROMPT_COMMAND and internal commands
  [[ "${BASH_COMMAND}" == "_log4ai_"* ]] && return
  [[ "${BASH_COMMAND}" == "log4ai"* ]] && return
  [[ "${BASH_COMMAND}" == "$PROMPT_COMMAND" ]] && return

  # Only capture on new commands (check history number)
  local histnum=$(history 1 | awk '{print $1}')
  [[ "$histnum" == "${_LOG4AI_LAST_HISTNUM}" ]] && return
  _LOG4AI_LAST_HISTNUM="$histnum"

  # Get the full command from history (more reliable than BASH_COMMAND for pipes)
  local cmd=$(history 1 | sed 's/^ *[0-9]* *//')

  # Skip blacklisted
  if _log4ai_is_blacklisted "$cmd"; then
    _LOG4AI_SKIP=true
    return
  fi

  _LOG4AI_SKIP=false
  _LOG4AI_CMD="$cmd"
  _LOG4AI_CWD="$(pwd)"
  _LOG4AI_START_MS=$(($(date +%s%N 2>/dev/null || echo $(date +%s)000000000) / 1000000))

  # Set up output capture
  _LOG4AI_OUTFILE=$(mktemp "${TMPDIR:-/tmp}/log4ai.XXXXXX")
  exec > >(tee -a "${_LOG4AI_OUTFILE}") 2>&1
}

# --------------------------------------------------------------------------
# PROMPT_COMMAND: Log after command completes
# --------------------------------------------------------------------------
_log4ai_prompt_command() {
  local exit_code=$?

  [[ "${LOG4AI_ENABLED}" != "true" ]] && return
  [[ "${_LOG4AI_SKIP}" == "true" ]] && return
  [[ -z "${_LOG4AI_CMD}" ]] && return

  # Restore stdout/stderr
  exec > /dev/tty 2>&1

  local end_ms=$(($(date +%s%N 2>/dev/null || echo $(date +%s)000000000) / 1000000))
  local duration_ms=$(( end_ms - ${_LOG4AI_START_MS:-$end_ms} ))
  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local hostname=$(hostname -s 2>/dev/null || hostname)

  # Read captured output
  local output=""
  if [[ -f "${_LOG4AI_OUTFILE}" ]]; then
    output=$(cat "${_LOG4AI_OUTFILE}")
    rm -f "${_LOG4AI_OUTFILE}"
  fi

  # Build JSON
  local json_cmd=$(_log4ai_json_escape "${_LOG4AI_CMD}")
  local json_output=$(_log4ai_json_escape "${output}")
  local json_cwd=$(_log4ai_json_escape "${_LOG4AI_CWD}")

  local logentry="{\"timestamp\":\"${timestamp}\",\"host\":\"${hostname}\",\"session\":\"${LOG4AI_SESSION_ID}\",\"cwd\":${json_cwd},\"command\":${json_cmd},\"exit_code\":${exit_code},\"duration_ms\":${duration_ms},\"output\":${json_output},\"shell\":\"bash\"}"

  # Append to today's JSONL
  echo "${logentry}" >> "$(_log4ai_logfile)"

  # Cleanup
  unset _LOG4AI_CMD _LOG4AI_START_MS _LOG4AI_CWD _LOG4AI_OUTFILE
}

# --------------------------------------------------------------------------
# Register hooks
# --------------------------------------------------------------------------
trap '_log4ai_debug_trap' DEBUG

# Prepend to PROMPT_COMMAND (preserve existing)
if [[ -z "$PROMPT_COMMAND" ]]; then
  PROMPT_COMMAND="_log4ai_prompt_command"
else
  PROMPT_COMMAND="_log4ai_prompt_command;${PROMPT_COMMAND}"
fi

# --------------------------------------------------------------------------
# Utility commands
# --------------------------------------------------------------------------
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

echo "[log4AI] BASH logger active | session: ${LOG4AI_SESSION_ID} | log4ai status for info"
