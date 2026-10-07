#!/usr/bin/env sh
# One-line installer for Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main/install.sh | sh
# Extra flags go after "-s --":  ... | sh -s -- --dry-run
set -e
REPO_RAW="https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main"
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3 is required. Install it (macOS: 'brew install python', Ubuntu: 'sudo apt install python3') and re-run." >&2
  exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$REPO_RAW/local_ai_installer.py" -o "$TMP/local_ai_installer.py"
# Re-attach the terminal so prompts work when piped from curl.
if [ -t 0 ]; then "$PY" "$TMP/local_ai_installer.py" "$@"; else "$PY" "$TMP/local_ai_installer.py" "$@" < /dev/tty; fi
