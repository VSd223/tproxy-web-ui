#!/usr/bin/env python3
"""Interactive console administration utility for tproxy-server with MTProxy promo tag."""
import json
import os
import re
import secrets
import subprocess
import sys

PROFILES = "/etc/tproxy-server/profiles.json"
LOG_FILE = "/var/log/tproxy/tproxy.log"
MTPROXY_ENV = "/etc/mtproxy/mtproxy.env"
PROTOCOLS = ("https", "https-lanes", "websocket", "websocket-lanes")


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    return (result.stdout.strip() or result.stderr.strip())


def show_logs(errors_only=False):
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as stream:
            lines = stream.readlines()[-200:]
            if errors_only:
                lines = [line for line in lines if any(word in line.lower() for word in (
                    "error", "err", "fatal", "panic", "failed", "failure", "denied", "ошиб"
                ))]
            print("".join(lines) or "Записей нет.")
    except OSError as exc:
        print(f"Не удалось прочитать {LOG_FILE}: {exc}")


def load():
    with open(PROFILES, encoding="utf-8") as stream:
        return json.load(stream)


def hostname():
    try:
        with open("/etc/tproxy-admin.env", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("PUBLIC_HOSTNAME="):
                    return line.rstrip().split("=", 1)[1]
    except OSError:
        pass
    return input("Домен сервера: ").strip()


def save(data):
    temporary = PROFILES + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, PROFILES)
    run("/usr/bin/install", "-o", "tproxy", "-g", "tproxy", "-m", "0400", PROFILES, "/run/tproxy-server/profiles.json")
    print(run("systemctl", "restart", "tproxy-server.service", "mtproxy.service"))


def show_profiles():
    host = hostname()
    print("\n--- Список устройств и профилей ---")
    for item in load().get("profiles", []):
        sec = item['secret']
        print(f"• [{item['name']}] Транспорт: {item['carrier_mode']}")
        print(f"  Секрет: {sec}")
        print(f"  Ссылка: https://t.me/webproxy?server={host}&secret={sec}")
        print(f"  Прямая: tg://webproxy?server={host}&secret={sec}\n")


def add_profile():
    name = input("Имя устройства/пользователя (например iPhone-Main): ").strip()
    backend = input("Backend [127.0.0.1:9067]: ").strip() or "127.0.0.1:9067"
    print("Доступные транспорты ядра:", ", ".join(PROTOCOLS))
    protocol = input("Протокол [https]: ").strip() or "https"
    if protocol not in PROTOCOLS:
        raise ValueError("Неподдерживаемый протокол")
    
    pad = input("Использовать dd... случайное дополнение (Random Padding)? [y/N]: ").strip().lower()
    raw_secret = secrets.token_hex(16)
    secret = ("dd" + raw_secret) if pad == "y" else raw_secret

    limit_str = input("Лимит устройств (max_sessions, 1 = строго одно устройство, 0 = без лимита) [1]: ").strip() or "1"
    max_sessions = int(limit_str)

    data = load()
    if any(item.get("name") == name for item in data["profiles"]):
        raise ValueError("Профиль с таким именем уже существует")
    
    new_item = {
        "name": name,
        "secret": secret,
        "backend": backend,
        "carrier_mode": protocol,
    }
    if max_sessions > 0:
        new_item["limits"] = {"max_sessions": max_sessions}

    data["profiles"].append(new_item)
    save(data)
    print(f"Устройство {name} добавлено успешно!")


def set_promo_channel():
    tag = input("Введите 32-hex тег от @MTProxybot (или пустую строку для удаления): ").strip().lower()
    if tag and (len(tag) != 32 or not re.match(r"^[0-9a-f]{32}$", tag)):
        print("Ошибка: Тег должен состоять ровно из 32 hex-символов!")
        return

    workers = "1"
    max_conn = "4096"
    if os.path.exists(MTPROXY_ENV):
        with open(MTPROXY_ENV, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MTPROXY_WORKERS="):
                    workers = line.strip().split("=", 1)[1]
                elif line.startswith("MTPROXY_MAX_CONNECTIONS="):
                    max_conn = line.strip().split("=", 1)[1]

    with open(MTPROXY_ENV, "w", encoding="utf-8") as f:
        f.write(f"MTPROXY_WORKERS={workers}\n")
        f.write(f"MTPROXY_MAX_CONNECTIONS={max_conn}\n")
        f.write(f"MTPROXY_TAG={tag}\n")
    
    run("systemctl", "restart", "mtproxy.service")
    print(f"Спонсорский тег установлен: {tag or 'отключен'}")


def main():
    if os.geteuid() != 0:
        print("Запустите от root: sudo tproxy-admin", file=sys.stderr)
        return 1
    while True:
        print("\n══════════════════════════════════════════")
        print("1) Статус служб       2) Список устройств и ссылок")
        print("3) Добавить устройство 4) Спонсорский канал (@MTProxybot)")
        print("5) Логи сервера       6) Только ошибки")
        print("7) Метрики            8) Перезапустить релей")
        print("0) Выход")
        print("══════════════════════════════════════════")
        choice = input("Выбор: ").strip()
        try:
            if choice == "1":
                print("Relay (tproxy):", run("systemctl", "is-active", "tproxy-server.service"))
                print("Admin panel:   ", run("systemctl", "is-active", "tproxy-admin.service"))
                print("MTProxy:       ", run("systemctl", "is-active", "mtproxy.service"))
            elif choice == "2":
                show_profiles()
            elif choice == "3":
                add_profile()
            elif choice == "4":
                set_promo_channel()
            elif choice == "5":
                show_logs(False)
            elif choice == "6":
                show_logs(True)
            elif choice == "7":
                print(run("curl", "-fsS", "http://127.0.0.1:8081/metrics"))
            elif choice == "8":
                print(run("systemctl", "restart", "tproxy-server.service", "mtproxy.service"))
            elif choice == "0":
                return 0
        except (OSError, ValueError, EOFError) as exc:
            print(f"Ошибка: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())