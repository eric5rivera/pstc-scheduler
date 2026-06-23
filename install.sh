#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pstc-scheduler"
REPO_URL="${PSTC_SCHEDULER_REPO_URL:-https://github.com/eric5rivera/pstc-scheduler.git}"
INSTALL_DIR="${PSTC_SCHEDULER_INSTALL_DIR:-$HOME/.local/share/$APP_NAME}"
PYTHON_BIN="${PYTHON:-}"
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

is_macos() { [[ "$(uname -s)" == "Darwin" ]]; }
is_linux() { [[ "$(uname -s)" == "Linux" ]]; }
is_ubuntu_like() {
  [[ -r /etc/os-release ]] || return 1
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" || "${ID:-}" == "debian" || "${ID_LIKE:-}" == *"ubuntu"* || "${ID_LIKE:-}" == *"debian"* ]]
}

have_sudo() {
  command -v sudo >/dev/null && sudo -v
}

python_works() {
  local candidate="$1"
  command -v "$candidate" >/dev/null || return 1
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

select_python_bin() {
  local candidate

  if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
    return 0
  fi

  for candidate in \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "python3"
  do
    if python_works "$candidate"; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done

  PYTHON_BIN="python3"
  return 1
}

install_ubuntu_dependencies() {
  echo "Checking Ubuntu/Debian dependencies..."

  if ! have_sudo; then
    echo "sudo is required to install missing system packages."
    exit 1
  fi

  sudo apt-get update
  sudo apt-get install -y git python3 python3-venv python3-pip ca-certificates
}

check_macos_dependencies() {
  if ! command -v git >/dev/null; then
    cat <<EOF
git is required.

On macOS, install Apple's Command Line Tools, then rerun this installer:
  xcode-select --install
EOF
    if command -v xcode-select >/dev/null; then
      xcode-select --install 2>/dev/null || true
    fi
    exit 1
  fi

  if [[ -z "$PYTHON_BIN" ]] || ! python_works "$PYTHON_BIN"; then
    cat <<EOF
python3 is required.

Install Python 3.10+ from https://www.python.org/downloads/macos/ or with Homebrew:
  brew install python

Then rerun this installer.
EOF
    exit 1
  fi
}

ensure_dependencies() {
  select_python_bin || true

  if is_linux && is_ubuntu_like; then
    if ! command -v git >/dev/null || ! python_works "$PYTHON_BIN" || ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
      install_ubuntu_dependencies
      select_python_bin || true
    fi
  elif is_macos; then
    check_macos_dependencies
  fi

  command -v git >/dev/null || { echo "git is required"; exit 1; }

  if ! python_works "$PYTHON_BIN"; then
    echo "Python 3.10+ is required, and the selected Python is not usable: $PYTHON_BIN"
    echo "If you use pyenv, your Python may be linked to a missing Homebrew library. Try: brew install gettext"
    exit 1
  fi

  if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    echo "Python venv support is required. On Ubuntu, install python3-venv."
    exit 1
  fi
}

ensure_dependencies

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "Updating $APP_NAME in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Installing $APP_NAME to $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

rm -rf "$INSTALL_DIR/.venv"
"$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"

if is_linux && is_ubuntu_like; then
  echo "Installing Playwright Chromium system dependencies..."
  "$INSTALL_DIR/.venv/bin/python" -m playwright install-deps chromium
fi

"$INSTALL_DIR/.venv/bin/python" -m playwright install chromium

ln -sf "$INSTALL_DIR/.venv/bin/pstc-scheduler" "$BIN_DIR/pstc-scheduler"

cat <<EOF

✅ $APP_NAME installed.

Run it with:
  pstc-scheduler

Installed command:
  $BIN_DIR/pstc-scheduler

If this shell does not find or autocomplete pstc-scheduler yet, refresh its command cache:
  hash -rf 2>/dev/null || rehash 2>/dev/null || hash -r 2>/dev/null || true
EOF

if ! path_contains "$BIN_DIR"; then
  cat <<EOF

$BIN_DIR is not currently on your PATH, so this shell will not find the command yet.
Run this now:
  export PATH="$BIN_DIR:\$PATH"

To make it permanent, add that same line to your shell profile, for example ~/.zshrc.
EOF
fi
