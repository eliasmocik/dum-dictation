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
    like="$ID_LIKE"
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
    sid=$(loginctl 2>/dev/null | awk -v u="$USER" '$0 ~ u {print $1; exit}') || true
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
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra-gtk3-module)
      PORTAUDIO=(portaudio19-dev)
      BUILD_PKGS=(cmake gcc g++)
      PYTHON_PKGS=(python3.12 python3.12-venv)
      TRAY_PKGS=(libappindicator3-1 gir1.2-appindicator3-0.1)
      PYTHON_ALT="python3"  # fallback if 3.12 not in repos
      PPA_NEEDED=0          # flag: need deadsnakes PPA?
      # Check if python3.12 is available
      if ! apt-cache show python3.12 &>/dev/null; then
        PPA_NEEDED=1
      fi
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
      TRAY_PKGS=(libappindicator)
      PPA_NEEDED=0
      ;;
    suse)
      BASE_PKGS=(xdotool xclip)
      WAYLAND_PKGS=(wl-clipboard ydotool)
      SOUND_PKGS=(libcanberra-gtk3-module)
      PORTAUDIO=(portaudio-devel)
      BUILD_PKGS=(cmake gcc gcc-c++)
      PYTHON_PKGS=(python312)
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
      if [[ "$dry_run" -eq 1 ]]; then
        echo "sudo apt-get install -y $*"
      else
        sudo apt-get install -y "$@"
      fi
      ;;
    fedora)
      if [[ "$dry_run" -eq 1 ]]; then
        echo "sudo dnf install -y $*"
      else
        sudo dnf install -y "$@"
      fi
      ;;
    arch)
      if [[ "$dry_run" -eq 1 ]]; then
        echo "sudo pacman -S --noconfirm $*"
      else
        sudo pacman -S --noconfirm "$@"
      fi
      ;;
    suse)
      if [[ "$dry_run" -eq 1 ]]; then
        echo "sudo zypper install -y $*"
      else
        sudo zypper install -y "$@"
      fi
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
    return
  fi

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
    # Wayland typing needs the ydotoold daemon; flag it if it isn't enabled.
    if [[ "$session" == "wayland" ]] && command -v ydotool >/dev/null 2>&1; then
      if ! systemctl --user is-enabled ydotoold >/dev/null 2>&1; then
        info "Wayland typing uses ydotool - start its daemon once with: ydotoold &"
        info "  (or enable a service). Without it, dum falls back to pynput typing."
      fi
    fi
  fi
}

main "$@"
