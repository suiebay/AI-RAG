#!/usr/bin/env bash
#
# One-shot deploy script for Ubuntu 24.04 as root.
# Clones the repo, installs everything, configures nginx + systemd,
# and starts the app on port 80.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/suiebay/AI-RAG/main/deploy.sh | bash
#
# You'll be prompted for GEMINI_API_KEY.

set -euo pipefail

APP_DIR="/opt/ai-rag"
APP_USER="ai-rag"
REPO_URL="https://github.com/suiebay/AI-RAG.git"
SERVICE_NAME="ai-rag"
PORT_INTERNAL="8001"

echo "==> Checking prerequisites"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy.sh" >&2
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx git curl ufw >/dev/null

echo "==> Creating service user"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Cloning/updating repo"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch --quiet
  git -C "${APP_DIR}" reset --hard origin/main --quiet
else
  rm -rf "${APP_DIR}"
  git clone --quiet "${REPO_URL}" "${APP_DIR}"
fi

echo "==> Setting up Python venv"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

echo "==> Configuring .env"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  read -rp "Paste your GEMINI_API_KEY: " GEMINI_KEY
  SECRET=$(head -c 32 /dev/urandom | base64)
  cat >"${APP_DIR}/.env" <<EOF
GEMINI_API_KEY=${GEMINI_KEY}
SECRET_KEY=${SECRET}
EOF
  chmod 600 "${APP_DIR}/.env"
  echo "    .env created"
else
  echo "    .env already exists, leaving as-is"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> Installing systemd service"
cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=AI-RAG Kazakh student chatbot
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn app:app --host 127.0.0.1 --port ${PORT_INTERNAL}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo "==> Configuring nginx"
cat >/etc/nginx/sites-available/${SERVICE_NAME} <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 4m;

    location / {
        proxy_pass http://127.0.0.1:${PORT_INTERNAL};
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null
systemctl restart nginx

echo "==> Opening firewall"
ufw --force enable >/dev/null 2>&1 || true
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 'Nginx Full' >/dev/null 2>&1 || true

echo "==> Waiting for app to become ready"
for i in {1..10}; do
  if curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:${PORT_INTERNAL}/login | grep -q 200; then
    break
  fi
  sleep 1
done

PUBLIC_IP=$(curl -fsS -4 ifconfig.me 2>/dev/null || echo "<server-ip>")
echo
echo "============================================================"
echo " Deploy complete"
echo " App URL:      http://${PUBLIC_IP}/"
echo " Service logs: journalctl -u ${SERVICE_NAME} -f"
echo " Restart:      systemctl restart ${SERVICE_NAME}"
echo " Demo accounts: admin/admin123  student/student123"
echo "============================================================"
