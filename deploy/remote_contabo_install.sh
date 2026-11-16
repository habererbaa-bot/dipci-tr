#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/dipci"
sudo mkdir -p "$APP_DIR/data"
sudo chown -R ubuntu:ubuntu "$APP_DIR"

tar -xzf /tmp/dipci_deploy.tar.gz -C "$APP_DIR"
rm -f /tmp/dipci_deploy.tar.gz

if [ ! -d "$APP_DIR/venv" ]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

sudo tee /etc/systemd/system/dipci.service > /dev/null <<'EOF'
[Unit]
Description=dipci.tr — Fiyat Rontgeni, Reyon Kamerasi ve Dogrulanmis Alisveris Agi (:8090)
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/dipci
EnvironmentFile=/opt/dipci/.env
ExecStart=/opt/dipci/venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 8090 --workers 2 --proxy-headers --no-server-header
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable dipci.service
sudo systemctl restart dipci.service
sleep 2
sudo systemctl is-active dipci.service

# Caddyfile güncellemesi (Yedekli + Doğrulamalı)
if ! sudo grep -q "^dipci.tr {" /etc/caddy/Caddyfile; then
  sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.dipci
  sudo tee -a /etc/caddy/Caddyfile > /dev/null <<'EOF'

# ── dipci.tr — DİPÇİ (fiyat röntgeni, reyon kamerası ve doğrulanmış alışveriş ağı, FastAPI :8090).
www.dipci.tr {
	redir https://dipci.tr{uri} permanent
}
dipci.tr {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8090 {
		import vekil
	}
}
EOF
  sudo caddy validate --config /etc/caddy/Caddyfile
  sudo systemctl reload caddy
fi

curl -fsS http://127.0.0.1:8090/robots.txt | head -n 4
echo "CONTABO_DEPLOY_SUCCESS"
