#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pstc-scheduler"
REPO_URL="${PSTC_SCHEDULER_REPO_URL:-https://github.com/eric5rivera/pstc-scheduler.git}"
INSTALL_DIR="${PSTC_SCHEDULER_INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
PYTHON_BIN="${PYTHON:-python3}"
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    -h|--help)
      cat <<EOF
Usage:
  install.sh              Install or update $APP_NAME
  install.sh --uninstall  Completely uninstall $APP_NAME and remove saved data
EOF
      exit 0
      ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

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

remove_empty_dir() {
  local dir="$1"
  if [[ -d "$dir" ]] && rmdir "$dir" 2>/dev/null; then
    echo "Removed empty directory $dir"
  fi
}

uninstall_app() {
  local target="$INSTALL_DIR/.venv/bin/$APP_NAME"
  local candidate
  local removed=0

  for candidate in \
    "$BIN_DIR/$APP_NAME" \
    "$HOME/.local/bin/$APP_NAME" \
    "/opt/homebrew/bin/$APP_NAME" \
    "/usr/local/bin/$APP_NAME"
  do
    if [[ -L "$candidate" && "$(readlink "$candidate")" == "$target" ]]; then
      rm -f "$candidate"
      echo "Removed $candidate"
      removed=1
    fi
  done

  if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR"
    removed=1
  fi

  if [[ -d "$HOME/.pstc-scheduler" ]]; then
    rm -rf "$HOME/.pstc-scheduler"
    echo "Removed $HOME/.pstc-scheduler"
    removed=1
  fi

  remove_empty_dir "$BIN_DIR"
  remove_empty_dir "$(dirname "$INSTALL_DIR")"
  remove_empty_dir "$HOME/.local"

  if [[ "$removed" == "0" ]]; then
    echo "$APP_NAME does not appear to be installed."
  else
    echo "✅ $APP_NAME completely uninstalled."
  fi
}

if [[ "$UNINSTALL" == "1" ]]; then
  uninstall_app
  exit 0
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
