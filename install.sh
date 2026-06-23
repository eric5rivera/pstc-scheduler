#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pstc-scheduler"
REPO_URL="${PSTC_SCHEDULER_REPO_URL:-https://github.com/YOUR_GITHUB_USERNAME/pstc-scheduler.git}"
INSTALL_DIR="${PSTC_SCHEDULER_INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
BIN_DIR="${PSTC_SCHEDULER_BIN_DIR:-$HOME/.local/bin}"
PYTHON_BIN="${PYTHON:-python3}"

if [[ "$REPO_URL" == *"YOUR_GITHUB_USERNAME"* ]]; then
  echo "Set PSTC_SCHEDULER_REPO_URL to your GitHub repo URL, or replace YOUR_GITHUB_USERNAME in install.sh."
  echo "Example: curl -fsSL https://raw.githubusercontent.com/USER/pstc-scheduler/main/install.sh | PSTC_SCHEDULER_REPO_URL=https://github.com/USER/pstc-scheduler.git bash"
  exit 1
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
  $BIN_DIR/pstc-scheduler

If that command is not found, add this to your shell profile:
  export PATH=\"$BIN_DIR:\$PATH\"
EOF
