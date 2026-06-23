#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pstc-scheduler"
REPO_URL="${PSTC_SCHEDULER_REPO_URL:-https://github.com/eric5rivera/pstc-scheduler.git}"
INSTALL_DIR="${PSTC_SCHEDULER_INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
PYTHON_BIN="${PYTHON:-python3}"

path_contains() {
  case ":$PATH:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ -n "${PSTC_SCHEDULER_BIN_DIR:-}" ]]; then
  BIN_DIR="$PSTC_SCHEDULER_BIN_DIR"
elif path_contains "$HOME/.local/bin"; then
  BIN_DIR="$HOME/.local/bin"
elif [[ -d /opt/homebrew/bin && -w /opt/homebrew/bin ]] && path_contains /opt/homebrew/bin; then
  BIN_DIR="/opt/homebrew/bin"
elif [[ -d /usr/local/bin && -w /usr/local/bin ]] && path_contains /usr/local/bin; then
  BIN_DIR="/usr/local/bin"
else
  BIN_DIR="$HOME/.local/bin"
fi

command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v "$PYTHON_BIN" >/dev/null || { echo "python3 is required"; exit 1; }

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "Updating $APP_NAME in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Installing $APP_NAME to $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

"$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/python" -m playwright install chromium

ln -sf "$INSTALL_DIR/.venv/bin/pstc-scheduler" "$BIN_DIR/pstc-scheduler"

cat <<EOF

✅ $APP_NAME installed.

Run it with:
  pstc-scheduler

Installed command:
  $BIN_DIR/pstc-scheduler
EOF

if ! path_contains "$BIN_DIR"; then
  cat <<EOF

$BIN_DIR is not currently on your PATH, so this shell will not find the command yet.
Run this now:
  export PATH="$BIN_DIR:\$PATH"

To make it permanent, add that same line to your shell profile, for example ~/.zshrc.
EOF
fi
