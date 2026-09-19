#!/usr/bin/env bash
set -euo pipefail

R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m' C='\033[0;36m' W='\033[1;37m' D='\033[0m'
ok()   { echo -e "${G}[✓]${D} $*"; }
info() { echo -e "${C}[i]${D} $*"; }
warn() { echo -e "${Y}[!]${D} $*"; }

[[ "${EUID}" -ne 0 ]] && { echo -e "${R}Запустите от root: sudo bash uninstall.sh${D}"; exit 1; }

echo -e "
${W}╔══════════════════════════════════════════════════════════╗
║${W}     tproxy-deploy — удаление всех компонентов           ${W}║
╚══════════════════════════════════════════════════════════╝${D}
"

read -rp "Точно удалить всё? [y/N]: " CONFIRM
[[ "${CONFIRM,,}" != "y" ]] && { echo "Отмена."; exit 0; }

info "Останавливаю сервисы..."
systemctl stop tproxy-admin.service tproxy-server.service mtproxy.service refresh-mtproxy-config.timer 2>/dev/null || true
systemctl disable tproxy-admin.service tproxy-server.service mtproxy.service refresh-mtproxy-config.timer 2>/dev/null || true
ok "Сервисы остановлены"

info "Удаляю systemd юниты..."
rm -f /etc/systemd/system/tproxy-server.service
rm -f /etc/systemd/system/tproxy-admin.service
rm -f /etc/systemd/system/mtproxy.service
rm -rf /etc/systemd/system/mtproxy.service.d
rm -f /etc/systemd/system/refresh-mtproxy-config.service
rm -f /etc/systemd/system/refresh-mtproxy-config.timer
systemctl daemon-reload
ok "Systemd юниты удалены"

info "Удаляю конфиги..."
rm -rf /etc/tproxy-server
rm -rf /etc/mtproxy
rm -f /etc/tproxy-admin.env
rm -f /etc/logrotate.d/tproxy
ok "Конфиги удалены"

info "Удаляю бинарники..."
rm -f /usr/local/bin/tproxy-server
rm -f /usr/local/sbin/refresh-mtproxy-config
rm -f /usr/local/sbin/tproxy-admin
rm -f /usr/local/sbin/mtproxy-launcher
rm -rf /opt/MTProxy
rm -rf /opt/tproxy-admin
rm -rf /opt/tproxy-sites
rm -rf /var/log/tproxy
ok "Бинарники удалены"

info "Удаляю сайт..."
rm -rf /srv/tproxy-site
ok "Сайт удалён"

info "Удаляю nginx vhost..."
rm -f /etc/nginx/conf.d/tproxy.conf
nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
ok "Nginx конфиги удалены"

info "Удаляю пользователей..."
userdel -r tproxy 2>/dev/null || true
userdel -r mtproxy 2>/dev/null || true
ok "Пользователи удалены"

info "Удаляю рабочую директорию..."
rm -rf /root/tproxy-work
ok "Рабочая директория удалена"

echo ""
echo -e "${G}══════════════════════════════════════════════════════════${D}"
echo -e "${G}  Всё полностью удалено!${D}"
echo -e "${G}══════════════════════════════════════════════════════════${D}"