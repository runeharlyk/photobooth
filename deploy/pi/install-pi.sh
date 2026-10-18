#!/usr/bin/env bash
# Installs or updates the photobooth on a Raspberry Pi from a GitHub release.
# Usage: install-pi.sh [--dry-run] [--auto] [vX.Y.Z]   (default: the latest release)
# --auto is what the nightly timer runs: it does nothing unless a newer release
# exists and the booth is on its idle screen, and it never prompts for a password.
set -euo pipefail

REPO="runeharlyk/photobooth"
APP_DIR="$HOME/.local/share/photobooth"
VENV="$APP_DIR/venv"
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
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    --auto) auto=true ;;
    v*) tag="$arg" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

run() {
  if $dry_run; then
    printf 'would run:'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

write_file() {
  local path="$1" content="$2"
  if $dry_run; then
    printf 'would write %s:\n%s\n' "$path" "$content"
  else
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" > "$path"
  fi
}

latest_tag() {
  curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

installed_version() {
  "$VENV/bin/python" -c 'from importlib.metadata import version; print(version("photobooth"))' 2>/dev/null || true
}

booth_is_busy() {
  local first_event
  first_event="$(curl -sN --max-time 3 http://127.0.0.1:8000/api/events 2>/dev/null | head -c 400 || true)"
  [ -n "$first_event" ] && ! grep -q '"screen": "idle"' <<<"$first_event"
}

sudo_cmd() {
  if $auto; then run sudo -n "$@"; else run sudo "$@"; fi
}

missing=()
for package in "${APT_PACKAGES[@]}"; do
  dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
done
if ((${#missing[@]})); then
  sudo_cmd apt-get install -y "${missing[@]}"
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  if $dry_run; then
    echo "would install uv from https://astral.sh/uv/install.sh"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
fi
export PATH="$HOME/.local/bin:$PATH"

[ -n "$tag" ] || tag="$(latest_tag)"
version="${tag#v}"
if $auto; then
  if [ "$(installed_version)" = "$version" ]; then
    echo "photobooth $tag is already installed"
    exit 0
  fi
  if booth_is_busy; then
    echo "the booth is in use; the update to $tag waits for the next run"
    exit 0
  fi
fi
wheel="photobooth-$version-py3-none-any.whl"
download="$APP_DIR/releases/$wheel"
echo "installing photobooth $tag"

run mkdir -p "$APP_DIR/releases"
run curl -fsSL -o "$download" "https://github.com/$REPO/releases/download/$tag/$wheel"
run uv venv --allow-existing --system-site-packages --python /usr/bin/python3 "$VENV"
run uv pip install --python "$VENV/bin/python" --reinstall-package photobooth "$download"

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
  http://127.0.0.1:8000/"
run chmod +x "$KIOSK"

write_file "$AUTOSTART" "$KIOSK &"

write_file "$UPDATE_UNIT" "[Unit]
Description=Photobooth update from the latest GitHub release
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'curl -fsSL https://github.com/$REPO/releases/latest/download/install-pi.sh | bash -s -- --auto'"

write_file "$UPDATE_TIMER" "[Unit]
Description=Nightly photobooth update

[Timer]
OnCalendar=*-*-* 04:00:00

[Install]
WantedBy=timers.target"

$auto || sudo_cmd loginctl enable-linger "$(id -un)"
run systemctl --user daemon-reload
run systemctl --user enable photobooth.service
run systemctl --user enable --now photobooth-update.timer
run systemctl --user restart photobooth.service
echo "photobooth $tag installed; the kiosk starts at the next login or reboot"
