#!/usr/bin/env bash
# Installs or updates the photobooth on a Raspberry Pi from a GitHub release.
# Usage: install-pi.sh [--dry-run] [--auto] [vX.Y.Z]   (default: the latest release)
#
# Each release gets its own venv under venvs/, and venv is a symlink to the active one. A new
# venv is built and smoke-tested beside the running one, the symlink is flipped atomically, and
# the service is restarted; the previous venv is kept for rollback and older ones are deleted.
# A failed install leaves the active version untouched and exits non-zero.
#
# --auto is what the nightly timer runs (through update.sh) and never prompts for a password.
# It changes nothing unless the release is a final X.Y.Z release strictly newer than the
# installed one (pre-releases are never installed automatically) and the booth is on its idle
# screen, checked before anything else and again right before the switch and the restart.
set -euo pipefail

REPO="runeharlyk/photobooth"
APP_DIR="$HOME/.local/share/photobooth"
VENV="$APP_DIR/venv"
VENVS="$APP_DIR/venvs"
LEGACY_VENV="$APP_DIR/venv.legacy"
UPDATER="$APP_DIR/update.sh"
CONFIG_DIR="$HOME/.config/photobooth"
CONFIG="$CONFIG_DIR/config.toml"
MEDIA_DIR="$HOME/photobooth-media"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/photobooth.service"
UPDATE_UNIT="$UNIT_DIR/photobooth-update.service"
UPDATE_TIMER="$UNIT_DIR/photobooth-update.timer"
KIOSK="$APP_DIR/kiosk.sh"
AUTOSTART="$HOME/.config/labwc/autostart"
APT_PACKAGES=(python3-picamera2 ffmpeg chromium-browser curl)

dry_run=false
auto=false
tag=""

run() {
  if $dry_run; then
    printf 'would run:'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

# Writes through a temporary file and a rename, so a script that is running while it is
# replaced (update.sh during an update) keeps reading its old copy.
write_file() {
  local path="$1" content="$2" mode="${3:-}"
  if $dry_run; then
    printf 'would write %s:\n%s\n' "$path" "$content"
  else
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" > "$path.tmp"
    [ -z "$mode" ] || chmod "$mode" "$path.tmp"
    mv -f "$path.tmp" "$path"
  fi
}

sudo_cmd() {
  if $auto; then run sudo -n "$@"; else run sudo "$@"; fi
}

latest_tag() {
  curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

installed_version() {
  "$VENV/bin/python" -c 'from importlib.metadata import version; print(version("photobooth"))' 2>/dev/null || true
}

# Prints "newer" when $1 is a final release (X.Y.Z) strictly newer than the installed version $2
# under PEP 440 ordering, "not-final" when $1 is anything else, and "not-newer" otherwise. An
# empty $2 means nothing is installed. Only $1 is restricted to final releases; $2 may carry
# pre-, post-, dev- or local segments, and a version with an epoch is never older than $1.
compare_versions() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

candidate, installed = sys.argv[1], re.sub(r"^0+!", "", sys.argv[2].lower())


def release(text):
    numbers = [int(part) for part in text.split(".")]
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return numbers


if not re.fullmatch(r"\d+(\.\d+)*", candidate):
    print("not-final")
    sys.exit()
if not installed:
    print("newer")
    sys.exit()
match = re.fullmatch(r"(\d+(?:\.\d+)*)([^+!]*)(\+.*)?", installed)
if not match:
    print("not-newer")
    sys.exit()
suffix = match.group(2)
below_its_release = bool(re.match(r"[-_.]?(a|b|c|rc|alpha|beta|pre|preview)\d*", suffix)) or (
    "dev" in suffix and "post" not in suffix
)
ahead = release(candidate) > release(match.group(1))
tied_above_prerelease = release(candidate) == release(match.group(1)) and below_its_release
print("newer" if ahead or tied_above_prerelease else "not-newer")
PY
}

# Busy unless the first event the booth streams says it is on its idle screen. A refused
# connection means the backend is not running, so no guest can be using the booth; a timeout,
# an empty stream or a garbled first event defers the update.
booth_is_busy() {
  local events status=0
  events="$(curl -sN --max-time 3 http://127.0.0.1:8000/api/events 2>/dev/null)" || status=$?
  [ "$status" -ne 7 ] || return 1
  ! python3 -c '
import json, sys
line = next((line for line in sys.stdin if line.startswith("data: ")), None)
sys.exit(0 if line is not None and json.loads(line[len("data: "):]).get("screen") == "idle" else 1)
' <<<"$events" 2>/dev/null
}

venv_works() {
  [ -x "$1/bin/python" ] && "$1/bin/python" -I -c 'import photobooth.__main__' >/dev/null 2>&1
}

active_venv() {
  if [ -L "$VENV" ]; then
    readlink "$VENV"
  elif [ -d "$VENV" ]; then
    echo "$LEGACY_VENV"
  fi
}

install_apt_packages() {
  local package missing=()
  for package in "${APT_PACKAGES[@]}"; do
    dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
  done
  if ((${#missing[@]})); then
    sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y "${missing[@]}"
  fi
}

install_uv() {
  if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    if $dry_run; then
      echo "would install uv from https://astral.sh/uv/install.sh"
    else
      curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
  fi
}

# Builds the venv for $version at $target from its release wheel and smoke-tests it.
build_venv() {
  local wheel="photobooth-$version-py3-none-any.whl"
  local download="$APP_DIR/releases/$wheel"
  run mkdir -p "$APP_DIR/releases" "$VENVS" \
    && run curl -fsSL -o "$download.part" "https://github.com/$REPO/releases/download/$tag/$wheel" \
    && run mv -f "$download.part" "$download" \
    && run rm -rf "$target" \
    && run uv venv --system-site-packages --python /usr/bin/python3 "$target" \
    && run uv pip install --python "$target/bin/python" "$download" \
    && run "$target/bin/python" -I -c 'import photobooth.__main__'
}

# Points the venv symlink at $1 with a single rename, moving a plain venv directory left by an
# earlier install aside to venv.legacy first.
switch_to() {
  local link="$APP_DIR/venv.switch"
  run ln -sfn "$1" "$link"
  if [ -d "$VENV" ] && [ ! -L "$VENV" ]; then
    run mv -T "$VENV" "$LEGACY_VENV"
  fi
  run mv -T "$link" "$VENV"
}

# Keeps the active venv and the one it replaced; a reinstall of the active version prunes nothing.
prune_venvs() {
  local dir
  [ "$previous" != "$target" ] || return 0
  for dir in "$VENVS"/* "$LEGACY_VENV"; do
    [ -e "$dir" ] || continue
    [ "$dir" = "$target" ] || [ "$dir" = "$previous" ] || run rm -rf "$dir"
  done
}

write_files() {
  if [ ! -f "$CONFIG" ]; then
    write_file "$CONFIG" "share_base_url = \"https://photo.example.invalid/p\"
media_dir = \"$MEDIA_DIR\"

[camera]
kind = \"picamera2\""
  fi

  write_file "$UNIT" "[Unit]
Description=Photobooth
After=network-online.target

[Service]
ExecStart=$VENV/bin/photobooth --config $CONFIG
Restart=always
RestartSec=2

[Install]
WantedBy=default.target"

  write_file "$KIOSK" "#!/usr/bin/env bash
until curl -fs -o /dev/null http://127.0.0.1:8000/; do sleep 1; done
exec chromium-browser --kiosk --incognito --noerrdialogs --disable-infobars --no-first-run \\
  --password-store=basic --ozone-platform=wayland --disable-features=Translate \\
  http://127.0.0.1:8000/" 755

  write_file "$AUTOSTART" "$KIOSK &"

  write_file "$UPDATER" "#!/usr/bin/env bash
# Runs the latest release's install script in --auto mode; photobooth-update.timer starts it nightly.
set -euo pipefail
script=\"\$(mktemp)\"
trap 'rm -f \"\$script\"' EXIT
curl -fsSL -o \"\$script\" https://github.com/$REPO/releases/latest/download/install-pi.sh
bash \"\$script\" --auto </dev/null" 755

  write_file "$UPDATE_UNIT" "[Unit]
Description=Photobooth update from the latest GitHub release
After=network-online.target

[Service]
Type=oneshot
ExecStart=$UPDATER"

  write_file "$UPDATE_TIMER" "[Unit]
Description=Nightly photobooth update

[Timer]
OnCalendar=*-*-* 04:00:00

[Install]
WantedBy=timers.target"
}

# Starts the kiosk in the desktop session that is already running, so a manual install needs no
# reboot; without a session, labwc's autostart starts it at the next login.
start_kiosk() {
  local runtime display
  runtime="/run/user/$(id -u)"
  display="$(find "$runtime" -maxdepth 1 -name 'wayland-[0-9]' -printf '%f\n' 2>/dev/null | head -n 1 || true)"
  if [ -z "$display" ]; then
    echo "no desktop session is running; the kiosk starts at the next login"
    return
  fi
  if $dry_run; then
    echo "would restart the kiosk on $display"
    return
  fi
  pkill -f "$KIOSK" 2>/dev/null || true
  pkill -f "chromium.*--kiosk.*127.0.0.1:8000" 2>/dev/null || true
  WAYLAND_DISPLAY="$display" XDG_RUNTIME_DIR="$runtime" setsid -f "$KIOSK" </dev/null >/dev/null 2>&1
  echo "kiosk started on $display"
}

# Everything runs from here, so bash has read the whole script before it acts: a truncated
# download fails to parse instead of running a prefix, and a command reading stdin cannot eat
# the rest of a script piped into bash (as the timer of earlier installs still does).
main() {
  local arg installed
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=true ;;
      --auto) auto=true ;;
      v*) tag="$arg" ;;
      *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
  done

  [ -n "$tag" ] || tag="$(latest_tag)"
  version="${tag#v}"
  installed="$(installed_version)"
  if $auto; then
    case "$(compare_versions "$version" "$installed")" in
      newer) ;;
      not-final)
        echo "photobooth $tag is not a final release; pre-releases are only installed by hand"
        exit 0 ;;
      *)
        echo "photobooth $installed is installed and $tag is not newer; nothing to do"
        exit 0 ;;
    esac
    if booth_is_busy; then
      echo "the booth is in use; the update to $tag waits for the next run"
      exit 0
    fi
  fi

  install_apt_packages
  install_uv
  export PATH="$HOME/.local/bin:$PATH"

  target="$VENVS/$version"
  previous="$(active_venv)"
  if venv_works "$target"; then
    echo "photobooth $version is already built at $target"
  else
    echo "installing photobooth $tag into $target"
    if ! build_venv; then
      [ "$target" = "$previous" ] || run rm -rf "$target"
      echo "photobooth $tag failed to install; the active version is unchanged" >&2
      exit 1
    fi
  fi

  if $auto && booth_is_busy; then
    echo "the booth came into use; photobooth $version is built but not switched to, and waits for the next run"
    exit 0
  fi

  write_files
  $auto || sudo_cmd loginctl enable-linger "$(id -un)"
  run systemctl --user daemon-reload
  run systemctl --user enable photobooth.service
  run systemctl --user enable --now photobooth-update.timer
  switch_to "$target"
  run systemctl --user restart photobooth.service
  prune_venvs

  if $auto; then
    echo "photobooth updated from ${installed:-nothing} to $version and restarted; ${previous:-no earlier venv} is kept for rollback"
  else
    echo "photobooth $tag installed and running"
    start_kiosk
  fi
}

main "$@"
