#!/usr/bin/env bash
# Выкладка проекта на сервер по SSH-ключу: ./deploy/deploy.sh <IP-сервера> [пользователь]
# Секреты (пароль администратора, ключ сессий) и журналы не копируются — на сервере создаются свои.
# Установка идет на сервере в фоне (nohup), поэтому обрыв SSH ее не прерывает; ход — в /root/dou-setup.log.
set -uo pipefail
HOST="$1"; USER_="${2:-root}"
KEY="${SSH_KEY:-$HOME/.ssh/aeza_hackathon}"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes -o ServerAliveInterval=15)
cd "$(dirname "$0")/.."

retry() {  # повторяет команду: публичные SSH-серверы под атакой ботов иногда сбрасывают соединения
  for i in $(seq 1 12); do "$@" && return 0; echo "  повтор $i…" >&2; sleep 5; done; return 1
}

tar czf /tmp/dou.tgz \
  --exclude=kb/.secret_key --exclude=kb/admin_password.txt --exclude=kb/audit.log.jsonl --exclude=kb/tunnel.log \
  --exclude=kb/index/chunks.json --exclude=kb/generated --exclude=kb/uploads --exclude=__pycache__ --exclude=.claude \
  --exclude='tests/answers_*' app web kb deploy requirements.txt README.md

echo "Копирую архив…"
retry scp "${SSH_OPTS[@]}" /tmp/dou.tgz "$USER_@$HOST:/tmp/dou.tgz" || { echo "Не удалось скопировать"; exit 1; }
echo "Запускаю установку на сервере…"
retry ssh "${SSH_OPTS[@]}" "$USER_@$HOST" \
  'mkdir -p /opt/dou && tar xzf /tmp/dou.tgz -C /opt/dou && nohup bash /opt/dou/deploy/setup.sh > /root/dou-setup.log 2>&1 &
   echo started' || exit 1
echo "Ход установки: ssh root@$HOST tail -f /root/dou-setup.log"
