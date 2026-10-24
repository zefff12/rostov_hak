#!/usr/bin/env bash
# Развертывание помощника на чистом Ubuntu 22.04/24.04 (запуск от root).
# Ожидает, что архив проекта уже распакован в /opt/dou.
set -euo pipefail

APP_DIR=/opt/dou
DOMAIN="${DOMAIN:-_}"   # домен, если есть; иначе сайт доступен по IP
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
# 7B требует ~6 ГБ ОЗУ; на маленьких серверах — 3B и урезанный контекст.
if [ -z "${LLM_MODEL:-}" ]; then
  if [ "$MEM_GB" -ge 12 ]; then LLM_MODEL=qwen2.5:7b; NUM_CTX=6144; else LLM_MODEL=qwen2.5:3b; NUM_CTX=4096; fi
fi
NUM_CTX="${NUM_CTX:-6144}"
echo "ОЗУ: ${MEM_GB} ГБ → модель $LLM_MODEL, контекст $NUM_CTX"

echo "[0/6] SSH: только ключи; IP, с которого идет развертывание, не штрафуется за чужие атаки"
DEPLOY_IP="${SSH_CLIENT%% *}"
cat >/etc/ssh/sshd_config.d/10-dou.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
${DEPLOY_IP:+PerSourcePenaltyExemptList $DEPLOY_IP}
EOF
sshd -t && systemctl reload ssh || rm -f /etc/ssh/sshd_config.d/10-dou.conf

echo "[0/6] Файл подкачки (страховка от нехватки памяти)"
if [ "$MEM_GB" -lt 12 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 6G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
fi

echo "[1/6] Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip nginx curl ufw

echo "[2/6] Ollama"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/limits.conf <<EOF
[Service]
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=2
EOF
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama
for i in $(seq 1 30); do curl -s http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done

echo "[3/6] Модели (первый раз ~6 ГБ)"
ollama pull "$LLM_MODEL"
ollama pull bge-m3

echo "[4/6] Python-окружение"
id dou >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin dou
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/kb/index" "$APP_DIR/kb/generated" "$APP_DIR/kb/uploads"
chown -R dou:dou "$APP_DIR"

echo "[5/6] Служба помощника"
cat >/etc/systemd/system/dou-assistant.service <<EOF
[Unit]
Description=DOU assistant (FastAPI)
After=network.target ollama.service
Wants=ollama.service

[Service]
User=dou
WorkingDirectory=$APP_DIR
Environment=LLM_MODEL=$LLM_MODEL
Environment=LLM_NUM_CTX=$NUM_CTX
Environment=LLM_KEEP_ALIVE=24h
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now dou-assistant
systemctl restart dou-assistant

echo "[6/6] Nginx и firewall"
cat >/etc/nginx/sites-available/dou <<EOF
server {
    listen 80 default_server;
    server_name $DOMAIN;
    client_max_body_size 12m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header CF-Connecting-IP \$remote_addr;
        # Потоковые ответы чата: без буферизации и с длинным таймаутом.
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/dou /etc/nginx/sites-enabled/dou
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null

echo "[7/7] Туннель Cloudflare: HTTPS-адрес без домена и сертификатов"
if ! command -v cloudflared >/dev/null; then
  curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
  dpkg -i /tmp/cloudflared.deb >/dev/null
fi
cat >/etc/systemd/system/dou-tunnel.service <<EOF
[Unit]
Description=Cloudflare quick tunnel for DOU assistant
After=network-online.target dou-assistant.service

[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:80
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now dou-tunnel
for i in $(seq 1 30); do
  URL=$(journalctl -u dou-tunnel --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)
  [ -n "$URL" ] && break; sleep 2
done
echo "Публичный HTTPS-адрес: ${URL:-см. journalctl -u dou-tunnel}"

sleep 3
curl -s http://127.0.0.1:8000/api/health && echo
echo "Готово. Пароль администратора: $APP_DIR/kb/admin_password.txt"
