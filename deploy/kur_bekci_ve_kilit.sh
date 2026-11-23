#!/usr/bin/env bash
# dipci.tr — Tek Yazıcı Kuyruk Kilidi (flock), Süre Sınırları ve 5 Dakikalık Otonom Bekçi Kurulumu
# DİKKAT: Yalnızca dipci* servislerine dokunur; başka hiçbir projeye (basinca vb.) dokunmaz.
set -euo pipefail

LOCK_FILE="/tmp/dipci_db_yazici.lock"

# 1) Tüm ağır arka plan DB yazıcılarına çakışma önleyici flock + idle I/O + azami süre sınırı ekle
declare -A JOBS=(
  ["dipci-reklamaction"]="4500|/opt/dipci/venv/bin/python3 /opt/dipci/reklamaction_feed.py"
  ["dipci-akakce-dusen"]="1500|/opt/dipci/venv/bin/python3 /opt/dipci/akakce_dusenler.py --sayfa 8 --en-fazla 60"
  ["dipci-akakce-katalog"]="1200|/opt/dipci/venv/bin/python3 /opt/dipci/akakce_katalog.py --istek 60 --sayfa 2 --bekle 15"
  ["dipci-denetim"]="1200|/opt/dipci/venv/bin/python3 /opt/dipci/denetim.py --uygula"
  ["dipci-gorsel"]="1200|/opt/dipci/venv/bin/python /opt/dipci/gorsel_tamamla.py 300"
  ["dipci-harvest"]="1800|/opt/dipci/venv/bin/python3 /opt/dipci/auto_catalog.py --harvest --supermarket-only --max-pages 10"
  ["dipci-online"]="1500|/opt/dipci/venv/bin/python3 /opt/dipci/auto_catalog.py --enrich-online 120 --magaza"
  ["dipci-radar"]="600|/opt/dipci/venv/bin/python3 /opt/dipci/fast_radar.py --run --vip-batch 45"
  ["dipci-sync"]="1500|/opt/dipci/venv/bin/python3 /opt/dipci/live_sync.py"
  ["dipci-tazele"]="600|/opt/dipci/venv/bin/python3 /opt/dipci/auto_catalog.py --tazele 12 --magaza"
)

for svc in "${!JOBS[@]}"; do
  if [ ! -f "/etc/systemd/system/${svc}.service" ]; then
    continue
  fi
  IFS='|' read -r max_sec cmd <<< "${JOBS[$svc]}"
  dropdir="/etc/systemd/system/${svc}.service.d"
  sudo mkdir -p "$dropdir"
  sudo tee "${dropdir}/10-tek-yazici.conf" > /dev/null <<EOF
[Service]
Nice=15
IOSchedulingClass=idle
RuntimeMaxSec=${max_sec}
ExecStart=
ExecStart=/usr/bin/flock -n ${LOCK_FILE} ${cmd}
EOF
done

# 2) 5 dakikalık otonom sağlık bekçisini (dipci-bekci.timer) kur
sudo tee /etc/systemd/system/dipci-bekci.service > /dev/null <<'EOF'
[Unit]
Description=dipci.tr Otonom Saglik Bekcisi (WAL, Takili Is ve Web Canlilik Onarimi)
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/dipci
EnvironmentFile=/opt/dipci/.env
RuntimeMaxSec=120
ExecStart=/opt/dipci/venv/bin/python3 /opt/dipci/dipci_bekci.py
EOF

sudo tee /etc/systemd/system/dipci-bekci.timer > /dev/null <<'EOF'
[Unit]
Description=Run dipci.tr Self-Healing Watchdog every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dipci-bekci.timer
echo "BEKCI_VE_KILIT_KURULDU"
