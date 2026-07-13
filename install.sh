#!/usr/bin/env bash
# dum dictation one-line installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/eliasmocik/dum-dictation/main/install.sh | bash
#
# What it does: clones the repo into ./dum-dictation (in whatever directory you're in)
# and runs ./setup. If ./dum-dictation already exists it leaves it alone and tells you
# what to do instead.
#
# Linux: also installs system packages (xdotool/xclip/ydotool/wl-clipboard etc.)
# automatically. Skip system deps: DUM_SKIP_SYS_DEPS=1 bash <(curl -fsSL ...)
#
# Note: this is piped from curl, so stdin is the script itself - it never reads from
# stdin and never asks questions. The whole thing lives in main(), called on the last
# line, so a connection that drops mid-download can't run a half-written script.

set -euo pipefail

REPO_URL="https://github.com/eliasmocik/dum-dictation.git"
DIR="dum-dictation"

main() {
  if ! command -v git >/dev/null 2>&1; then
    echo "You need git for this." >&2
    echo "  macOS: xcode-select --install" >&2
    echo "  Linux: sudo apt install git  (or your distro's equivalent)" >&2
    echo "Then rerun this installer." >&2
    exit 1
  fi

  if [ -e "$DIR" ]; then
    echo "There's already a '$DIR' here - not touching it."
    echo
    echo "If that's a previous install, just run:"
    echo "  cd $DIR && ./setup"
    echo "or update it first with:"
    echo "  cd $DIR && git pull && ./setup"
    exit 0
  fi

  echo "Cloning dum-dictation..."
  git clone "$REPO_URL" "$DIR"

  echo
  echo "Running setup (venv + deps + models - this is the long part)..."
  cd "$DIR"
  ./setup

  echo
  echo "Done. Next step:"
  echo "  cd dum-dictation && ./dum"
}

main "$@"
