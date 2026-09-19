#!/usr/bin/env bash
set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────
R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m' B='\033[0;34m'
C='\033[0;36m' M='\033[0;35m' W='\033[1;37m' D='\033[0m'
ok()   { echo -e "${G}[✓]${D} $*"; }
fail() { echo -e "${R}[✗]${D} $*"; exit 1; }
info() { echo -e "${C}[i]${D} $*"; }
ask()  { echo -e "${W}$1${D}"; }

# ─── Root check ──────────────────────────────────────────────────────────
[[ "${EUID}" -ne 0 ]] && fail "Запустите от root: sudo bash install.sh"

# ─── Скачивание файлов панели и сайтов ──────────────────────────────────
# Скачиваем ресурсы ИМЕННО из ВАШЕГО репозитория
ASSETS_REPO="https://github.com/VSd223/tproxy-web-ui"

info "Скачиваю компоненты Web-панели и сайты..."
TEMP_DIR="$(mktemp -d /tmp/tproxy-deploy.XXXXXX)"
trap 'rm -rf "$TEMP_DIR"' EXIT
curl -sL "${ASSETS_REPO}/archive/refs/heads/main.tar.gz" | tar -xz -C "$TEMP_DIR" --strip-components=1
DEPLOY_DIR="$TEMP_DIR"
ok "Файлы панели загружены"

GATEWAY="nginx"
if systemctl is-active --quiet caddy 2>/dev/null || command -v caddy >/dev/null 2>&1; then
  GATEWAY="caddy"
fi

echo -e "
${B}╔══════════════════════════════════════════════════════════╗
║${W}     TProxy Web UI — Скрытный WEB-прокси для Telegram   ${B}║
║${W}          (HTTP/2, HTTP/3, WebSocket, Защита от сканеров)${B}║
╚══════════════════════════════════════════════════════════╝${D}
"

# ─── Site selection ──────────────────────────────────────────────────────
ask "Выберите стартовый сайт для маскировки (в панели можно менять в 1 клик):"
echo -e "  ${C}1${D}) История ВКонтакте"
echo -e "  ${C}2${D}) История Telegram"
echo -e "  ${C}3${D}) История Instagram"
echo -e "  ${C}4${D}) История YouTube"
echo -e "  ${C}5${D}) История Одноклассников"
echo -e "  ${C}6${D}) О кошках"
echo -e "  ${C}7${D}) О собаках"
echo -e "  ${C}8${D}) О белых песцах"
echo -e "  ${C}9${D}) О музыке"
echo -e "  ${C}10${D}) О программировании"
echo ""
read -rp "Номер [1-10]: " SITE_NUM
SITE_NUM="${SITE_NUM:-2}"

declare -A SITES=(
  [1]="vk" [2]="telegram" [3]="instagram" [4]="youtube" [5]="odnoklassniki"
  [6]="cats" [7]="dogs" [8]="foxes" [9]="music" [10]="coding"
)
SITE_KEY="${SITES[$SITE_NUM]:-telegram}"

# ─── Domain & email ─────────────────────────────────────────────────────
read -rp "Домен сервера (например proxy.example.com): " DOMAIN
[[ -z "$DOMAIN" ]] && fail "Домен не может быть пустым"

# Вернул вопрос про почту, но с автозаполнением!
read -rp "Email для SSL (Enter = admin@${DOMAIN}): " EMAIL
EMAIL="${EMAIL:-admin@${DOMAIN}}"

# ─── Secret Admin Path Generation ───────────────────────────────────────
DEFAULT_ADMIN_PATH="panel_$(openssl rand -hex 5)"
ask "Секретный путь для веб-панели (для защиты от сканеров):"
read -rp "Путь [${DEFAULT_ADMIN_PATH}]: " USER_ADMIN_PATH
ADMIN_PATH="${USER_ADMIN_PATH:-$DEFAULT_ADMIN_PATH}"
ADMIN_PATH="${ADMIN_PATH#/}"
ADMIN_PATH="${ADMIN_PATH%/}"

# ─── Management Mode ───────────────────────────────────────────────────
WEB_PANEL="yes"
CARRIER="https"
PROMO_TAG=""

# ─── Install dependencies ───────────────────────────────────────────────
info "Устанавливаю системные зависимости..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PACKAGES=(
  curl git build-essential libssl-dev zlib1g-dev
  ca-certificates nftables python3 python3-pip
)
if [[ "$GATEWAY" == "nginx" ]]; then
  PACKAGES+=(nginx certbot python3-certbot-nginx)
fi
apt-get install -y -qq --no-install-recommends "${PACKAGES[@]}" >/dev/null 2>&1
ok "Зависимости установлены"

# ─── Go check ───────────────────────────────────────────────────────────
if ! command -v go &>/dev/null; then
  info "Устанавливаю Go 1.23..."
  GO_VER="1.23.1"
  rm -rf /usr/local/go
  curl -sL "https://go.dev/dl/go${GO_VER}.linux-amd64.tar.gz" | tar -C /usr/local -xzf -
  echo 'export PATH=$PATH:/usr/local/go/bin' > /etc/profile.d/go.sh
  export PATH=$PATH:/usr/local/go/bin
  ok "Go установлен"
fi

# ─── Clone & Build ──────────────────────────────────────────────────────
WORKDIR="/root/tproxy-work"
mkdir -p "$WORKDIR"
if [[ -d "$WORKDIR/tproxy-server/.git" ]]; then
  git -C "$WORKDIR/tproxy-server" pull --quiet 2>/dev/null || true
else
  git clone --depth 1 https://github.com/telegramdesktop/tproxy-server.git "$WORKDIR/tproxy-server" --quiet
fi

info "Собираю tproxy-server..."
cd "$WORKDIR/tproxy-server"
go build -trimpath -o tproxy-server ./cmd/tproxy-server || fail "Ошибка сборки tproxy-server"
install -m 0755 tproxy-server /usr/local/bin/tproxy-server
ok "tproxy-server собран"

info "Собираю официальный MTProxy..."
bash deploy/install-mtproxy.sh 2>&1 | tail -1 || fail "Ошибка сборки MTProxy"
ok "MTProxy собран"

# ─── User & System Setup ────────────────────────────────────────────────
id tproxy &>/dev/null || useradd --system --home /nonexistent --shell /usr/sbin/nologin tproxy
id mtproxy &>/dev/null || useradd --system --home /nonexistent --shell /usr/sbin/nologin mtproxy
mkdir -p /etc/tproxy-server /etc/mtproxy /var/log/tproxy /opt/tproxy-admin /opt/tproxy-sites /srv/tproxy-site

# ─── Download Telegram DC Configs ──────────────────────────────────────
[[ ! -s /etc/mtproxy/proxy-secret ]] && curl -fsSL https://core.telegram.org/getProxySecret -o /etc/mtproxy/proxy-secret
[[ ! -s /etc/mtproxy/proxy-multi.conf ]] && curl -fsSL https://core.telegram.org/getProxyConfig -o /etc/mtproxy/proxy-multi.conf

# ─── Save/Preserve Database & Secrets ──────────────────────────────────
SECRET="dd$(openssl rand -hex 16)"
ADMIN_USER="admin"
ADMIN_PASSWORD=$(openssl rand -hex 16)
ADMIN_SALT=$(openssl rand -hex 16)
ADMIN_PASSWORD_HASH=$(ADMIN_PASSWORD="$ADMIN_PASSWORD" ADMIN_SALT="$ADMIN_SALT" python3 -c 'import hashlib,os; print(os.environ["ADMIN_SALT"]+"$"+hashlib.pbkdf2_hmac("sha256",os.environ["ADMIN_PASSWORD"].encode(),bytes.fromhex(os.environ["ADMIN_SALT"]),210000).hex())')

if [[ ! -s /etc/tproxy-server/token.key ]]; then
  TOKEN_UMASK="$(umask)"
  umask 077
  dd if=/dev/urandom of=/etc/tproxy-server/token.key bs=32 count=1 status=none
  umask "$TOKEN_UMASK"
fi
chmod 0600 /etc/tproxy-server/token.key

if [[ ! -s /etc/tproxy-server/profiles.json ]]; then
  cat > /etc/tproxy-server/profiles.json <<PROF
{
  "profiles": [
    {
      "name": "Основное устройство",
      "secret": "${SECRET}",
      "backend": "127.0.0.1:9067",
      "carrier_mode": "${CARRIER}",
      "limits": {
        "max_sessions": 1,
        "max_streams": 32,
        "max_streams_per_session": 32
      }
    }
  ]
}
PROF
fi
chmod 0644 /etc/tproxy-server/profiles.json

cat > /etc/tproxy-server/config.json <<CONF
{
  "public_hostname": "${DOMAIN}",
  "listen": "127.0.0.1:8080",
  "admin_listen": "127.0.0.1:8081",
  "public_dir": "/srv/tproxy-site",
  "static_routes": "exact",
  "token_key_file": "/run/tproxy-server/token.key",
  "profiles_file": "/run/tproxy-server/profiles.json",
  "enable_pprof": false,
  "limits": {
    "max_header_bytes": 16384,
    "max_body_bytes": 2097152,
    "max_frame_payload": 1048576,
    "carrier_batch_bytes": 2097152,
    "max_streams_per_session": 128,
    "max_closed_stream_ids": 4096,
    "max_pending_per_session": 33554432,
    "max_pending_global": 536870912,
    "max_pending_items_per_session": 16384,
    "max_pending_items_global": 262144,
    "max_sessions_per_ip": 0,
    "max_sessions_global": 256,
    "max_streams_global": 4096,
    "max_backend_dials_in_flight": 256,
    "new_sessions_per_minute": 1200,
    "new_sessions_burst": 256,
    "new_streams_per_minute": 12000,
    "new_streams_burst": 1024,
    "max_bootstraps_per_ip": 0,
    "max_bootstraps_global": 512,
    "new_bootstraps_per_minute": 1200,
    "new_bootstraps_burst": 256,
    "max_profiles": 64
  },
  "timeouts": {
    "backend_dial": "5s",
    "long_poll": "25s",
    "reconnect_grace": "2m",
    "bootstrap_lifetime": "2m",
    "read_header": "10s",
    "idle": "75s",
    "shutdown": "15s"
  }
}
CONF
chmod 0644 /etc/tproxy-server/config.json

if [[ ! -s /etc/mtproxy/mtproxy.env ]]; then
  cat > /etc/mtproxy/mtproxy.env <<MTEOF
MTPROXY_WORKERS=1
MTPROXY_MAX_CONNECTIONS=4096
MTPROXY_TAG=${PROMO_TAG}
MTEOF
fi
chmod 0644 /etc/mtproxy/mtproxy.env

# ─── Python MTProxy Launcher ──────────────────────────────────────────
cat > /usr/local/sbin/mtproxy-launcher <<'EOF'
#!/usr/bin/env python3
import json, os, re, sys

env_file = "/etc/mtproxy/mtproxy.env"
env_vars = {}
if os.path.exists(env_file):
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip("\"'")
    except Exception: pass

workers = env_vars.get("MTPROXY_WORKERS", "1")
max_conn = env_vars.get("MTPROXY_MAX_CONNECTIONS", "4096")
tag = env_vars.get("MTPROXY_TAG", "").strip().lower()

cmd = [
    "/opt/MTProxy/objs/bin/mtproto-proxy",
    "-u", "mtproxy",
    "-p", "8888",
    "-H", "9067",
    "-C", str(max_conn),
    "-M", str(workers),
]

secrets_found = set()
profiles_file = "/etc/tproxy-server/profiles.json"
if os.path.exists(profiles_file):
    try:
        with open(profiles_file, "r", encoding="utf-8") as f:
            for p in json.load(f).get("profiles", []):
                sec = str(p.get("secret", "")).strip().lower()
                if sec.startswith("dd") and len(sec) == 34: sec = sec[2:]
                if len(sec) == 32 and re.match(r"^[0-9a-f]{32}$", sec):
                    secrets_found.add(sec)
    except Exception: pass

for s in secrets_found:
    cmd.extend(["-S", s])

if tag and len(tag) == 32 and re.match(r"^[0-9a-f]{32}$", tag):
    cmd.extend(["-P", tag])

cmd.extend(["--aes-pwd", "/etc/mtproxy/proxy-secret", "/etc/mtproxy/proxy-multi.conf"])
os.execv(cmd[0], cmd)
EOF
chmod 0755 /usr/local/sbin/mtproxy-launcher

# ─── Copy All Camouflage Presets ────────────────────────────────────────
info "Копирую шаблоны сайтов в /opt/tproxy-sites..."
cp -r "${DEPLOY_DIR}/sites/"* /opt/tproxy-sites/ 2>/dev/null || true

if [[ ! -f /srv/tproxy-site/index.html ]]; then
  cp -r "${DEPLOY_DIR}/sites/_shared/." /srv/tproxy-site/ 2>/dev/null || true
  cp -r "${DEPLOY_DIR}/sites/${SITE_KEY}/." /srv/tproxy-site/ 2>/dev/null || true
  echo "${SITE_KEY}" > /srv/tproxy-site/.site_preset
fi
chmod -R a+rX /srv/tproxy-site /opt/tproxy-sites

# ─── Install Admin Panel API & Web UI ───────────────────────────────────
install -m 0755 "${DEPLOY_DIR}/admin/admin.py" /opt/tproxy-admin/admin.py
install -m 0755 "${DEPLOY_DIR}/admin/adminctl.py" /usr/local/sbin/tproxy-admin
install -m 0644 "${DEPLOY_DIR}/admin/admin.html" /opt/tproxy-admin/admin.html

if [[ ! -s /etc/tproxy-admin.env ]]; then
  cat > /etc/tproxy-admin.env <<AEOF
ADMIN_USER=${ADMIN_USER}
ADMIN_PASSWORD_HASH=${ADMIN_PASSWORD_HASH}
ADMIN_PATH=${ADMIN_PATH}
ADMIN_PROFILES=/etc/tproxy-server/profiles.json
ADMIN_METRICS=http://127.0.0.1:8081/metrics
ADMIN_SITE=/opt/tproxy-admin/admin.html
ADMIN_LOG_FILE=/var/log/tproxy/tproxy.log
PUBLIC_HOSTNAME=${DOMAIN}
WEB_PANEL=${WEB_PANEL}
AEOF
  chmod 0600 /etc/tproxy-admin.env
fi

# ─── Systemd Services Setup ─────────────────────────────────────────────
cat > /etc/systemd/system/mtproxy.service <<'MSVC'
[Unit]
Description=Official Telegram MTProto Proxy Backend
After=network.target

[Service]
Type=simple
User=root
StandardOutput=append:/var/log/tproxy/tproxy.log
StandardError=append:/var/log/tproxy/tproxy.log
ExecStart=/usr/local/sbin/mtproxy-launcher
Restart=always
RestartSec=3s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
MSVC

cat > /etc/systemd/system/tproxy-server.service <<TSVC
[Unit]
Description=Telegram WEB Proxy Relay
After=network-online.target mtproxy.service
Wants=network-online.target mtproxy.service

[Service]
Type=simple
User=tproxy
Group=tproxy
StandardOutput=append:/var/log/tproxy/tproxy.log
StandardError=append:/var/log/tproxy/tproxy.log
RuntimeDirectory=tproxy-server
RuntimeDirectoryMode=0750
PermissionsStartOnly=true
ExecStartPre=/usr/bin/install -o tproxy -g tproxy -m 0400 /etc/tproxy-server/token.key /run/tproxy-server/token.key
ExecStartPre=/usr/bin/install -o tproxy -g tproxy -m 0400 /etc/tproxy-server/profiles.json /run/tproxy-server/profiles.json
ExecStart=/usr/local/bin/tproxy-server -config /etc/tproxy-server/config.json
Restart=always
RestartSec=3s
TimeoutStopSec=20s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
TSVC

cat > /etc/systemd/system/tproxy-admin.service <<ASVC
[Unit]
Description=tproxy web administration API
After=network-online.target tproxy-server.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
StandardOutput=append:/var/log/tproxy/tproxy.log
StandardError=append:/var/log/tproxy/tproxy.log
EnvironmentFile=/etc/tproxy-admin.env
ExecStart=/usr/bin/python3 /opt/tproxy-admin/admin.py
Restart=always
RestartSec=3s
ReadWritePaths=/etc/tproxy-server /etc/mtproxy /srv/tproxy-site /opt/tproxy-sites

[Install]
WantedBy=multi-user.target
ASVC

install -m 0644 "$WORKDIR/tproxy-server/deploy/refresh-mtproxy-config.service" /etc/systemd/system/ 2>/dev/null || true
install -m 0644 "$WORKDIR/tproxy-server/deploy/refresh-mtproxy-config.timer" /etc/systemd/system/ 2>/dev/null || true
install -m 0755 "$WORKDIR/tproxy-server/deploy/refresh-mtproxy-config.sh" /usr/local/sbin/refresh-mtproxy-config 2>/dev/null || true

# ─── Web Gateway (Nginx / Caddy) Setup ──────────────────────────────────
if [[ "$GATEWAY" == "caddy" ]]; then
  CADDYFILE="/etc/caddy/Caddyfile"
  cat > "$CADDYFILE" <<CADDYEOF
${DOMAIN} {
    tls ${EMAIL}
    
    handle /${ADMIN_PATH} {
        redir /${ADMIN_PATH}/ 308
    }
    handle_path /${ADMIN_PATH}/* {
        reverse_proxy 127.0.0.1:9090
    }
    
    handle {
        reverse_proxy 127.0.0.1:8080
    }
}
CADDYEOF
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
else
  # Nginx config with Custom Secret Admin Location
  cat > "/etc/nginx/conf.d/tproxy.conf" <<NGINXEOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location /${ADMIN_PATH}/ {
        proxy_pass http://127.0.0.1:9090/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF

  nginx -t && systemctl reload nginx 2>/dev/null || systemctl restart nginx
  info "Выпускаю SSL-сертификат Let's Encrypt через Certbot..."
  certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect 2>/dev/null || true
fi

# ─── Enable & Start All Services on Boot ────────────────────────────────
info "Включаю автозапуск всех служб при перезагрузке..."
systemctl daemon-reload
systemctl enable mtproxy.service tproxy-server.service tproxy-admin.service refresh-mtproxy-config.timer
if [[ "$GATEWAY" == "caddy" ]]; then
  systemctl enable caddy
else
  systemctl enable nginx
fi

systemctl restart mtproxy.service tproxy-server.service tproxy-admin.service
ok "Все службы запущены и добавлены в автозагрузку!"

# ─── Summary ────────────────────────────────────────────────────────────
echo -e "
${G}══════════════════════════════════════════════════════════${D}
${G}  Установка успешно завершена!                           ${D}
${G}══════════════════════════════════════════════════════════${D}

${W}Сайт-маскировка:${D}      https://${DOMAIN}
${W}Секретная веб-панель:${D} https://${DOMAIN}/${ADMIN_PATH}/
${W}Логин:${D}                 ${ADMIN_USER}
${W}Пароль:${D}                ${ADMIN_PASSWORD}

${W}Ссылка для Telegram (WEB-прокси):${D}
https://t.me/webproxy?server=${DOMAIN}&secret=${SECRET}

${C}Управление в терминале:${D} sudo tproxy-admin
"
