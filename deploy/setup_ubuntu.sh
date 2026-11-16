#!/usr/bin/env bash
# dipci.tr — Ubuntu / Debian Sunucu Tek Komut Kurulum Betiği (Systemd + Nginx + Let's Encrypt SSL)
set -euo pipefail

APP_DIR="/opt/dipci-tr"
DOMAIN="dipci.tr"

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx

sudo mkdir -p "$APP_DIR"
sudo cp -r ./* "$APP_DIR/"
cd "$APP_DIR"

sudo python3 -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

# Systemd Servisi
sudo tee /etc/systemd/system/dipci.service > /dev/null <<EOF
[Unit]
Description=dipci.tr FastAPI Fiyat Rontgeni ve Sosyal Ag Sunucusu
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 8090 --workers 2 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo chown -R www-data:www-data "$APP_DIR"
sudo systemctl daemon-reload
sudo systemctl enable --now dipci.service

# Nginx Reverse Proxy (Kamera WebRTC için HTTPS hazır)
sudo tee /etc/nginx/sites-available/dipci.tr > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/dipci.tr /etc/nginx/sites-enabled/dipci.tr
sudo nginx -t && sudo systemctl reload nginx

echo "✅ dipci.tr servisi kuruldu! DNS yönlendikten sonra SSL için çalıştırın:"
echo "sudo certbot --nginx -d dipci.tr -d www.dipci.tr"
