#!/usr/bin/env bash
# install-linux-deps.sh - detect distro and install system packages for dum-dictation.
#
# Usage:
#   scripts/install-linux-deps.sh              # interactive sudo prompt
#   scripts/install-linux-deps.sh --dry-run    # print what would be installed
#   DUM_SKIP_SYS_DEPS=1 ./dum                   # skip entirely
#
# Detects the Linux distribution and installs the correct set of packages for
# X11 or Wayland sessions. Safe to re-run (package managers skip already-installed).
set -euo pipefail

# ---- helpers ----------------------------------------------------------------
warn() { echo "[!] $*" >&2; }
info() { echo "    $*"; }
die()  { warn "$*"; exit 1; }

# ---- distro detection -------------------------------------------------------
detect_distro() {
  local id like
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    id="$ID"
    like="${ID_LIKE:-}"
  elif [[ -f /etc/arch-release ]]; then
    id="arch"; like=""
  else
    die "unsupported Linux distro - /etc/os-release not found"
  fi
  # Normalize to family
  case "$id" in
    debian|ubuntu|linuxmint|pop|elementary|zorin|kali)
      echo "debian"; return ;;
    fedora|rhel|centos|rocky|alma)
      echo "fedora"; return ;;
    arch|manjaro|endeavouros|garuda)
      echo "arch";   return ;;
    opensuse*|suse)
      echo "suse";   return ;;
  esac
  # Fall back to ID_LIKE
  case "$like" in
    *debian*)  echo "debian"; return ;;
    *fedora*)  echo "fedora"; return ;;
    *arch*)    echo "arch";   return ;;
    *suse*)    echo "suse";   return ;;
  esac
  # Known distros without a traditional package manager
  if [[ "$id" == "nixos" ]]; then
    echo "nixos"; return
  fi
  die "unsupported distro: $id ($like). Please install deps manually, see README.md"
}

# ---- session type detection -------------------------------------------------
detect_session() {
  local st
  st="${XDG_SESSION_TYPE:-}"
  if [[ -z "$st" ]]; then
    # Fall back to logind, matching the current user's session.
    local sid
      sid=$(loginctl 2>/dev/null | awk -v u="$USER" '$3==u {print $1; exit}') || true
    if [[ -n "$sid" ]]; then
      st=$(loginctl show-session "$sid" -p Type 2>/dev/null | cut -d= -f2 || true)
    fi
  fi
  case "${st,,}" in
    wayland) echo "wayland" ;;
    x11|tty) echo "x11" ;;
    *)       echo "x11" ;;  # safe default
  esac
}

# ---- package map ------------------------------------------------------------
pkg_map() {
  local distro="$1" session="$2"
  case "$distro" in
    debian)
      BASE_PKGS=(xdotool xclip)
      # ydotool is the primary Wayland typing backend but Debian doesn't package it
      # (Ubuntu/Fedora/Arch do). wtype IS in Debian main and is daemon-less, so it
      # covers sentence typing; pull both so a missing ydotool isn't fatal on Debian.
      WAYLAND_PKGS=(wl-clipboard ydotool wtype)
      SOUND_PKGS=(libcanberra-gtk3-module)
      PORTAUDIO=(portaudio19-dev)
      BUILD_PKGS=(cmake gcc g++)
      PYTHON_ALT="python3"  # fallback if 3.12 not in repos
      # Prefer python3.12 when it is packaged. Ubuntu 22.04 etc. only have it via the
      # deadsnakes PPA (PPA_NEEDED=1). Modern Debian (trixie/sid) ships 3.13 and has no
      # python3.12 package at all, so fall back to the default python3 + its venv there
      # (./setup accepts 3.12 or 3.13).
      osid=""
      if [[ -f /etc/os-release ]]; then
        osid=$(. /etc/os-release; echo "$ID")
      fi
      if apt-cache show python3.12 &>/dev/null; then
        PYTHON_PKGS=(python3.12 python3.12-venv)
        PPA_NEEDED=0
      elif [[ "$osid" == "ubuntu" ]]; then
        PYTHON_PKGS=(python3.12 python3.12-venv)
        PPA_NEEDED=1
      else
        PYTHON_PKGS=(python3 python3-venv)
        PPA_NEEDED=0
      fi
      PY_DEV_PKG="${PYTHON_PKGS[0]}-dev"   # Python headers - evdev (pynput dep) compiles against them
      # Tray icon (AppIndicator). The old libappindicator3-1 / gir1.2-appindicator3-0.1 were
      # removed on Ubuntu 24.04 / recent Debian; the ayatana fork replaces them. Installs are
      # per-package and non-fatal, so older releases that lack these just warn-and-skip.
      TRAY_PKGS=(libayatana-appindicator3-1 gir1.2-ayatanaappindicator3-0.1)
      ;;
    fedora)
      # Pre-query: is python3.12 available?
      if dnf list available python3.12 &>/dev/null 2>&1; then
        PY_PKG="python3.12"
        PY_VENV=""  # bundled
      else
        PY_PKG="python3"
        info "python3.12 not in repos - will use system python3 (may need manual upgrade)"
      fi
      BASE_PKGS=(xdotool xclip)
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra-gtk3)
      PORTAUDIO=(portaudio-devel)
      BUILD_PKGS=(cmake gcc gcc-c++)
      PYTHON_PKGS=("$PY_PKG")
      PY_DEV_PKG="${PY_PKG}-devel"   # Python headers - evdev (pynput dep) compiles against them
      TRAY_PKGS=(libappindicator-gtk3)
      PPA_NEEDED=0
      ;;
    arch)
      BASE_PKGS=(xdotool xclip)
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra)
      PORTAUDIO=(portaudio)
      BUILD_PKGS=(cmake gcc)
      PYTHON_PKGS=(python)  # Arch ships latest Python
      TRAY_PKGS=(libayatana-appindicator)
      PPA_NEEDED=0
      ;;
    suse)
      BASE_PKGS=(xdotool xclip)
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra-gtk3-module)
      PORTAUDIO=(portaudio-devel)
      BUILD_PKGS=(cmake gcc gcc-c++)
      PYTHON_PKGS=(python312)
      PY_DEV_PKG=python312-devel   # Python headers - evdev (pynput dep) compiles against them
      TRAY_PKGS=(libayatana-appindicator1)
      PPA_NEEDED=0
      ;;
    nixos)
      BASE_PKGS=(xdotool xclip)
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra-gtk3)
      PORTAUDIO=(portaudio)
      BUILD_PKGS=(cmake gcc)
      PYTHON_PKGS=(python312)
      TRAY_PKGS=(libappindicator-gtk3)
      PPA_NEEDED=0
      ;;
  esac

  PKGS=("${BASE_PKGS[@]}")
  if [[ "$session" == "wayland" ]]; then
    PKGS+=("${WAYLAND_PKGS[@]}")
  fi
  PKGS+=("${SOUND_PKGS[@]}" "${PORTAUDIO[@]}" "${BUILD_PKGS[@]}" "${PYTHON_PKGS[@]}" "${TRAY_PKGS[@]}")
  # Python dev headers - evdev (a pynput dependency) compiles a C extension against them.
  if [[ -n "${PY_DEV_PKG:-}" ]]; then
    PKGS+=("${PY_DEV_PKG}")
  fi
}

# ---- package manager commands -----------------------------------------------
is_installed() {
  # Returns 0 if the named package is already installed (per the active manager).
  case "$DISTRO" in
    debian) dpkg -s "$1" >/dev/null 2>&1 ;;
    fedora) rpm -q "$1" >/dev/null 2>&1 ;;
    arch)   pacman -Q "$1" >/dev/null 2>&1 ;;
    suse)   rpm -q "$1" >/dev/null 2>&1 ;;
    nixos)  return 1 ;;  # no reliable query; treat all as "to install"
    *)      return 1 ;;
  esac
}

install_cmd() {
  local distro="$1" dry_run="$2"
  shift 2
  local failed=()
  # Install each package individually so one bad name (e.g. renamed on a given
  # distro) doesn't abort the whole batch; failed packages are warned and skipped.
  case "$distro" in
    debian)
      if [[ "$PPA_NEEDED" -eq 1 ]]; then
        info "python3.12 not in official repos - adding deadsnakes PPA first..."
        if [[ "$dry_run" -eq 1 ]]; then
          info "  would run: sudo add-apt-repository -y ppa:deadsnakes/ppa"
        else
          sudo add-apt-repository -y ppa:deadsnakes/ppa || warn "PPA add failed - python3.12 may be unavailable"
          sudo apt-get update -qq || true
        fi
      fi
      for pkg in "$@"; do
        if [[ "$dry_run" -eq 1 ]]; then
          echo "  sudo apt-get install -y $pkg"
        else
          sudo apt-get install -y "$pkg" || { warn "  install failed: $pkg"; failed+=("$pkg"); }
        fi
      done
      ;;
    fedora)
      for pkg in "$@"; do
        if [[ "$dry_run" -eq 1 ]]; then
          echo "  sudo dnf install -y $pkg"
        else
          sudo dnf install -y "$pkg" || { warn "  install failed: $pkg"; failed+=("$pkg"); }
        fi
      done
      ;;
    arch)
      for pkg in "$@"; do
        if [[ "$dry_run" -eq 1 ]]; then
          echo "  sudo pacman -S --noconfirm $pkg"
        else
          sudo pacman -S --noconfirm "$pkg" || { warn "  install failed: $pkg"; failed+=("$pkg"); }
        fi
      done
      ;;
    suse)
      for pkg in "$@"; do
        if [[ "$dry_run" -eq 1 ]]; then
          echo "  sudo zypper install -y $pkg"
        else
          sudo zypper install -y "$pkg" || { warn "  install failed: $pkg"; failed+=("$pkg"); }
        fi
      done
      ;;
    nixos)
      if [[ "$dry_run" -eq 1 ]]; then
        echo "nix-shell -p $*"
      else
        warn "NixOS detected. Add these to your environment.systemPackages or use nix-shell:"
        warn "  $*"
        warn "Or enter a temporary shell: nix-shell -p $*"
        # Don't fail - just warn and continue
      fi
      ;;
    *)
      if [[ "$dry_run" -eq 1 ]]; then
        echo "# Unknown distro - you will need to install these manually:"
        printf '  - %s\n' "$@"
      else
        warn "unknown distro - install these packages manually:"
        printf '  - %s\n' "$@"
      fi
      ;;
  esac
  if [[ ${#failed[@]} -gt 0 ]]; then
    warn "Some packages failed to install: ${failed[*]}"
    warn "dum may still run - install the above manually if needed."
  fi
}

# ---- post-install configuration (idempotent, always run) --------------------
# These steps must run every time - even when all packages are already installed -
# because they configure system state (systemd drop-in, group membership) that a
# plain "packages present" check says nothing about. Keeping them out of the
# early-return path is what makes re-running this script actually converge.

configure_ydotool() {
  # Wayland typing needs the ydotoold daemon AND a socket the unprivileged user can
  # reach. ydotoold runs as root and creates its socket 0600, so we enable the unit
  # and drop in an ExecStartPost chmod. Without this, typing silently falls back to
  # pynput, which needs X and fails under Wayland.
  local session="$1" dry_run="$2"
  [[ "$session" == "wayland" ]] || return 0
  command -v ydotool >/dev/null 2>&1 || return 0

  # Capture first, THEN grep: piping systemctl straight into `grep -q` makes grep close
  # the pipe on the first match, systemctl take SIGPIPE (exit 141), and `set -o pipefail`
  # report the whole pipeline as failed - which would wrongly claim the unit is missing.
  local unit_files
  unit_files=$(systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null || true)
  if ! grep -q '^ydotool\.service' <<<"$unit_files"; then
    warn "Wayland typing uses ydotool - no ydotool.service unit found; start the daemon"
    warn "  manually once with: sudo ydotoold &   (or create a user service)"
  elif [[ "$dry_run" -eq 1 ]]; then
    info "would enable ydotool.service + drop in a socket chmod (Wayland typing)"
  else
    sudo systemctl enable ydotool.service 2>/dev/null || true
    sudo mkdir -p /etc/systemd/system/ydotool.service.d || true
    printf '[Service]\nExecStartPost=/bin/sh -c "chmod 0666 %s 2>/dev/null || true"\n' \
      "/tmp/.ydotool_socket" \
      | sudo tee /etc/systemd/system/ydotool.service.d/override.conf >/dev/null || true
    sudo systemctl daemon-reload || true
    sudo systemctl restart ydotool.service 2>/dev/null || sudo systemctl start ydotool.service 2>/dev/null || true
    info "ydotoold enabled + socket opened for your user - Wayland typing will work (survives reboot)."
  fi

  if ! command -v wl-copy >/dev/null 2>&1; then
    warn "wl-clipboard not installed - paste won't work under Wayland (it's in the Wayland package set)."
  fi
}

configure_input_group() {
  # The evdev hotkey reads /dev/input directly (so it works under Wayland); that needs
  # the 'input' group. Add the user and remind them to log out/in for it to take effect.
  local dry_run="$1"
  command -v getent >/dev/null 2>&1 || return 0
  getent group input >/dev/null 2>&1 || return 0
  if groups "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx input; then
    info "'$USER' is already in the 'input' group (evdev hotkey can read keyboards)."
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    info "would add '$USER' to the 'input' group (needed for the evdev hotkey)."
    return 0
  fi
  info "Adding '$USER' to the 'input' group (needed for the evdev hotkey to read keyboards)."
  sudo gpasswd -a "$USER" input \
    || warn "couldn't add to the input group - run manually: sudo usermod -aG input $USER"
  info "  LOG OUT and back in for the input-group membership to apply."
}

# ---- main -------------------------------------------------------------------
main() {
  local dry_run=0
  for arg in "$@"; do
    case "$arg" in
      --dry-run|--dryrun) dry_run=1 ;;
      --help|-h) echo "Usage: $0 [--dry-run]"; exit 0 ;;
    esac
  done

  echo "==> detecting Linux distribution..."
  local distro
  distro=$(detect_distro)
  DISTRO="$distro"   # exported for is_installed()
  echo "    distro family: $distro"

  local session
  session=$(detect_session)
  echo "    session type:  $session"

  # Load package list
  pkg_map "$distro" "$session"

  # Filter out empty entries, then drop packages already installed.
  local to_install=()
  for pkg in "${PKGS[@]}"; do
    [[ -n "$pkg" ]] && to_install+=("$pkg")
  done

  if [[ ${#to_install[@]} -eq 0 ]]; then
    echo "    no packages to install"
    return
  fi

  echo ""
  echo "==> checking currently installed tools..."
  local missing=()
  for pkg in "${to_install[@]}"; do
    if is_installed "$pkg"; then
      info "  already installed: $pkg"
    else
      missing+=("$pkg")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "    all required packages already installed"
  else
    echo "    packages to install: ${missing[*]}"
    echo ""
    if [[ "$dry_run" -eq 1 ]]; then
      echo "==> would install (--dry-run):"
      install_cmd "$distro" 1 "${missing[@]}"
    else
      # Verify sudo access
      if ! sudo -v &>/dev/null; then
        die "sudo required to install system packages. Run with DUM_SKIP_SYS_DEPS=1 to skip, then manually install: ${missing[*]}"
      fi
      echo "==> installing system dependencies..."
      # Keep sudo active during long installs
      while true; do sudo -n true; sleep 60; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &
      install_cmd "$distro" 0 "${missing[@]}"
      echo ""
      echo "    done: system dependencies installed"
    fi
  fi

  # Post-install configuration - ALWAYS runs (idempotent), even when every package was
  # already present, so re-running the script actually converges the system to a working
  # state instead of no-opping. These need sudo only when they change something.
  echo ""
  echo "==> configuring Wayland typing + hotkey access..."
  if [[ "$dry_run" -ne 1 ]] && [[ ${#missing[@]} -eq 0 ]]; then
    sudo -v &>/dev/null || warn "some config steps need sudo; skipping those you can't authorize."
  fi
  configure_ydotool "$session" "$dry_run"
  configure_input_group "$dry_run"
}

main "$@"
