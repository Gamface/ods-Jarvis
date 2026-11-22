#!/bin/bash
# One-time setup for jarvis-studio (no sudo needed). Safe to re-run.
#   bash setup.sh
# 1. Python venv in ~/ods/data/jarvis-studio/venv
# 2. Token + settings in ~/ods/data/jarvis-studio/studio.env (mode 600)
# 3. Enables Audacity's mod-script-pipe and OBS's websocket server (local only)
# 4. Installs and starts the jarvis-studio and dashboard systemd user services
# 5. Prints the Hermes mcp_servers entry
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HOME/ods/data/jarvis-studio"
ENV="$DATA/studio.env"
UV="${UV:-$HOME/.local/bin/uv}"
mkdir -p "$DATA"

echo "==> venv"
[ -x "$DATA/venv/bin/python" ] || "$UV" venv -q -p 3.12 "$DATA/venv"
"$UV" pip install -q -p "$DATA/venv/bin/python" "mcp>=1.9,<2" httpx websockets uvicorn numpy

echo "==> settings"
if [ ! -f "$ENV" ]; then
    umask 077
    cat > "$ENV" <<EOF
JARVIS_STUDIO_TOKEN=$(openssl rand -hex 24)
JARVIS_STUDIO_BIND=172.19.0.1
JARVIS_STUDIO_PORT=7721
JARVIS_OBS_PASSWORD=$(openssl rand -hex 12)
EOF
fi
chmod 600 "$ENV"
# shellcheck disable=SC1090
source "$ENV"

echo "==> Audacity mod-script-pipe"
ACFG="$HOME/.config/audacity/audacity.cfg"
mkdir -p "$(dirname "$ACFG")"
touch "$ACFG"
if ! grep -q '^mod-script-pipe=1' "$ACFG"; then
    python3 - "$ACFG" <<'PY'
import configparser, sys
p = sys.argv[1]
c = configparser.RawConfigParser(); c.optionxform = str; c.read(p)
import datetime, os
so = "/usr/lib/audacity/modules/mod-script-pipe.so"
stamp = datetime.datetime.fromtimestamp(os.path.getmtime(so)).strftime("%Y-%m-%dT%H:%M:%S")
for sec, key, val in [("Module", "mod-script-pipe", "1"), ("ModulePath", "mod-script-pipe", so),
                      ("ModuleDateTime", "mod-script-pipe", stamp)]:
    if not c.has_section(sec): c.add_section(sec)
    c.set(sec, key, val)
with open(p, "w") as f: c.write(f, space_around_delimiters=False)
PY
fi

echo "==> OBS websocket (127.0.0.1:4455)"
OCFG="$HOME/.config/obs-studio/plugin_config/obs-websocket/config.json"
mkdir -p "$(dirname "$OCFG")"
python3 - "$OCFG" "$JARVIS_OBS_PASSWORD" <<'PY'
import json, os, sys
p, pw = sys.argv[1], sys.argv[2]
cfg = json.load(open(p)) if os.path.exists(p) else {}
cfg.update({"server_enabled": True, "server_port": 4455, "auth_required": True,
            "server_password": pw, "alerts_enabled": False, "first_load": False})
json.dump(cfg, open(p, "w"), indent=4)
os.chmod(p, 0o600)
PY

echo "==> systemd user service"
mkdir -p "$HOME/.config/systemd/user"
for unit in jarvis-studio jarvis-studio-dashboard jarvis-llm-warmup; do
    sed "s#@HERE@#$HERE#g" "$HERE/$unit.service" > "$HOME/.config/systemd/user/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable --now jarvis-studio.service jarvis-studio-dashboard.service jarvis-llm-warmup.service
systemctl --user restart jarvis-studio.service jarvis-studio-dashboard.service
sleep 3
curl -fsS "http://$JARVIS_STUDIO_BIND:$JARVIS_STUDIO_PORT/health" && echo
curl -fsS "http://127.0.0.1:7722/health" && echo "  dashboard: http://127.0.0.1:7722"

cat <<EOF

Done. Hermes entry (~/ods/data/hermes/config.yaml):

mcp_servers:
  jarvis-studio:
    url: http://$JARVIS_STUDIO_BIND:$JARVIS_STUDIO_PORT/mcp
    headers:
      Authorization: "Bearer <JARVIS_STUDIO_TOKEN from $ENV>"
    timeout: 3600
EOF

# Command-line wrapper:  jarvis-studio practice "song" --mute guitar --speed 0.8
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/jarvis-studio" <<EOW
#!/bin/bash
set -a; source "$ENV"; set +a
cd "$HERE" && exec "$DATA/venv/bin/python" -m studio "\$@"
EOW
chmod +x "$HOME/.local/bin/jarvis-studio"
echo "CLI installed: jarvis-studio --help"

# App-menu launcher for the dashboard
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/jarvis-studio.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=Jarvis Studio
Comment=Dashboard for StemDeck, the studio apps and local AI music
Exec=xdg-open http://127.0.0.1:7722
Icon=multimedia-audio-player
Categories=AudioVideo;Audio;
EOD
echo "Dashboard: http://127.0.0.1:7722 (also in the app menu as Jarvis Studio)"
