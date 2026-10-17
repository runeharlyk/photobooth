#!/usr/bin/env bash
# Installs or updates the photobooth on a Raspberry Pi from a GitHub release.
# Usage: install-pi.sh [--dry-run] [vX.Y.Z]   (default: the latest release)
set -euo pipefail

REPO="runeharlyk/photobooth"
APP_DIR="$HOME/.local/share/photobooth"
VENV="$APP_DIR/venv"
CONFIG_DIR="$HOME/.config/photobooth"
CONFIG="$CONFIG_DIR/config.toml"
MEDIA_DIR="$HOME/photobooth-media"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/photobooth.service"
KIOSK="$APP_DIR/kiosk.sh"
AUTOSTART="$HOME/.config/labwc/autostart"
APT_PACKAGES=(python3-picamera2 ffmpeg chromium-browser curl)

dry_run=false
tag=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
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

missing=()
for package in "${APT_PACKAGES[@]}"; do
  dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
done
if ((${#missing[@]})); then
  run sudo apt-get install -y "${missing[@]}"
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

run sudo loginctl enable-linger "$(id -un)"
run systemctl --user daemon-reload
run systemctl --user enable photobooth.service
run systemctl --user restart photobooth.service
echo "photobooth $tag installed; the kiosk starts at the next login or reboot"
