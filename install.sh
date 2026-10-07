#!/usr/bin/env sh
# One-line installer for Linux / macOS:
#   curl -fsSL https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.sh | sh
# Options go after "-s --":   curl -fsSL ... | sh -s -- --dry-run
set -e
REPO_RAW="https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main"
export PYTHONUTF8=1

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; }

PY=""
for c in python3 python; do
  p=$(command -v "$c" 2>/dev/null || true)
  if [ -n "$p" ] && py_ok "$p"; then PY="$p"; break; fi
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ -z "$PY" ]; then
  echo "Python 3 not found - fetching a private copy with uv (nothing system-wide changes)..."
  UVDIR="$HOME/.local_ai_installer/uv"
  mkdir -p "$UVDIR"
  curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$UVDIR" sh
  "$UVDIR/uv" python install 3.12
  PY=$("$UVDIR/uv" python find 3.12)
fi

curl -fsSL "$REPO_RAW/local_ai_installer.py" -o "$TMP/local_ai_installer.py"

# When piped from curl, stdin is this script, so re-attach the keyboard for the installer's questions.
if [ -t 0 ]; then
  "$PY" "$TMP/local_ai_installer.py" "$@"
elif ( : < /dev/tty ) 2>/dev/null; then
  "$PY" "$TMP/local_ai_installer.py" "$@" < /dev/tty
else
  "$PY" "$TMP/local_ai_installer.py" "$@"
fi
