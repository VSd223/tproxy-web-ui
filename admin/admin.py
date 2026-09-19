#!/usr/bin/env python3
"""Loopback admin API with dynamic admin path, persistent profiles, dd-secrets, and site presets."""
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

USERNAME = os.environ.get("ADMIN_USER", "admin")
PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_PATH = os.environ.get("ADMIN_PATH", "admin").strip("/")
PROFILES = os.environ.get("ADMIN_PROFILES", "/etc/tproxy-server/profiles.json")
METRICS = os.environ.get("ADMIN_METRICS", "http://127.0.0.1:8081/metrics")
SITE_HTML = os.environ.get("ADMIN_SITE", "/opt/tproxy-admin/admin.html")
LOG_FILE = os.environ.get("ADMIN_LOG_FILE", "/var/log/tproxy/tproxy.log")
PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "")
MTPROXY_ENV = "/etc/mtproxy/mtproxy.env"
PUBLIC_DIR = "/srv/tproxy-site"

SEARCH_PRESET_DIRS = [
    "/opt/tproxy-sites",
    "/root/tproxy-work/tg-web/sites",
    "/root/tg-web-main/sites",
    os.path.join(os.path.dirname(__file__), "..", "sites"),
    os.path.join(os.path.dirname(__file__), "sites")
]

MAX_BODY = 15 * 1024 * 1024
PROTOCOLS = ("https", "https-lanes", "websocket", "websocket-lanes")
SESSIONS = {}

PRESET_NAMES = {
    "telegram": "История Telegram",
    "vk": "История ВКонтакте",
    "instagram": "История Instagram",
    "youtube": "История YouTube",
    "odnoklassniki": "История Одноклассников",
    "cats": "О кошках",
    "dogs": "О собаках",
    "foxes": "О белых песцах",
    "music": "О музыке",
    "coding": "О программировании"
}


def find_presets_dir():
    for d in SEARCH_PRESET_DIRS:
        if os.path.isdir(d) and os.path.isdir(os.path.join(d, "telegram")):
            return os.path.abspath(d)
    return "/opt/tproxy-sites"


def run(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return (result.stdout.strip() or result.stderr.strip()), result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc), 1


def password_ok(password):
    try:
        if not PASSWORD_HASH or "$" not in PASSWORD_HASH:
            return False
        salt, expected = PASSWORD_HASH.split("$", 1)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), 210_000
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def read_metrics():
    try:
        with urlopen(METRICS, timeout=3) as response:
            text = response.read(512 * 1024).decode("utf-8", "replace")
    except Exception as exc:
        return {"available": False, "error": str(exc), "values": {}, "profile_sessions": {}}

    values = {}
    profile_sessions = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " " not in line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            k, v = parts[0], parts[1]
            try:
                val_num = float(v)
                values[k] = val_num
                if "tproxy_sessions_live" in k and "profile=" in k:
                    match = re.search(r'profile="([^"]+)"', k)
                    if match:
                        profile_sessions[match.group(1)] = int(val_num)
            except ValueError:
                continue

    return {
        "available": True,
        "values": values,
        "profile_sessions": profile_sessions
    }


def read_logs(lines=200, errors_only=False):
    try:
        if not os.path.exists(LOG_FILE):
            return {"lines": [], "file": LOG_FILE, "errors_only": errors_only}
        with open(LOG_FILE, encoding="utf-8", errors="replace") as stream:
            content = stream.readlines()[-lines:]
        result = [line.rstrip() for line in content]
        if errors_only:
            result = [line for line in result if re.search(
                r"\b(error|err|fatal|panic|failed|failure|denied|ошиб)", line, re.IGNORECASE
            )]
        return {"lines": result, "file": LOG_FILE, "errors_only": errors_only}
    except OSError as exc:
        return {"lines": [], "file": LOG_FILE, "error": str(exc)}


def load_profiles_data():
    try:
        if not os.path.exists(PROFILES):
            return []
        with open(PROFILES, encoding="utf-8") as stream:
            data = json.load(stream)
        return data.get("profiles", [])
    except Exception:
        return []


def save_profiles_data(profiles_list):
    if not isinstance(profiles_list, list):
        raise ValueError("Профили должны быть списком")
    names = set()
    validated = []
    for p in profiles_list:
        name = str(p.get("name", "")).strip()
        secret = str(p.get("secret", "")).strip().lower()
        backend = str(p.get("backend", "127.0.0.1:9067")).strip()
        carrier = str(p.get("carrier_mode", "https")).strip()

        if not name:
            raise ValueError("Имя устройства не может быть пустым")
        if name in names:
            raise ValueError(f"Дубликат имени устройства: {name}")
        names.add(name)

        if carrier not in PROTOCOLS:
            raise ValueError(f"Неизвестный протокол: {carrier}")
            
        if len(secret) not in (32, 34) or not re.match(r"^[0-9a-f]+$", secret):
            raise ValueError(f"Секрет для {name} должен быть 32 hex или 34 hex (с dd)")

        limits = p.get("limits") or {}
        max_sessions = int(p.get("max_sessions", limits.get("max_sessions", 0)))
        max_streams = int(p.get("max_streams", limits.get("max_streams", 0)))

        item = {
            "name": name,
            "secret": secret,
            "backend": backend or "127.0.0.1:9067",
            "carrier_mode": carrier
        }

        if max_sessions > 0 or max_streams > 0:
            item["limits"] = {}
            if max_sessions > 0:
                item["limits"]["max_sessions"] = max_sessions
            if max_streams > 0:
                item["limits"]["max_streams"] = max_streams

        validated.append(item)

    temp_file = PROFILES + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as stream:
        json.dump({"profiles": validated}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temp_file, 0o644)
    os.replace(temp_file, PROFILES)
    
    run("/usr/bin/install", "-o", "tproxy", "-g", "tproxy", "-m", "0400", PROFILES, "/run/tproxy-server/profiles.json")
    run("systemctl", "restart", "tproxy-server.service", "mtproxy.service")
    return validated


def get_promo_tag():
    if not os.path.exists(MTPROXY_ENV):
        return ""
    try:
        with open(MTPROXY_ENV, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MTPROXY_TAG="):
                    return line.strip().split("=", 1)[1].strip('"\'')
    except Exception:
        pass
    return ""


def set_promo_tag(tag):
    tag = tag.strip().lower()
    if tag and not re.match(r"^[0-9a-f]{32}$", tag):
        raise ValueError("Тег прокси должен быть ровно 32 hex-символа от @MTProxybot")
    
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
    return tag


def apply_site_preset(key):
    presets_dir = find_presets_dir()
    if key == "random":
        available = [k for k in PRESET_NAMES.keys() if os.path.isdir(os.path.join(presets_dir, k))]
        if not available:
            available = list(PRESET_NAMES.keys())
        key = random.choice(available)

    src = os.path.join(presets_dir, key)
    shared = os.path.join(presets_dir, "_shared")

    os.makedirs(PUBLIC_DIR, exist_ok=True)
    for item in os.listdir(PUBLIC_DIR):
        item_path = os.path.join(PUBLIC_DIR, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)

    if os.path.isdir(shared):
        shutil.copytree(shared, PUBLIC_DIR, dirs_exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, PUBLIC_DIR, dirs_exist_ok=True)

    with open(os.path.join(PUBLIC_DIR, ".site_preset"), "w", encoding="utf-8") as f:
        f.write(key)

    run("chmod", "-R", "a+rX", PUBLIC_DIR)
    run("systemctl", "restart", "tproxy-server.service")
    return key


class Handler(BaseHTTPRequestHandler):
    server_version = "tproxy-admin/4.1"

    def log_message(self, *_args):
        return

    def send_json(self, status, value, extra_headers=None):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        if extra_headers:
            for name, val in extra_headers.items():
                self.send_header(name, val)
        self.end_headers()
        self.wfile.write(payload)

    def get_raw_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Недопустимый размер запроса")
        return self.rfile.read(length)

    def body_json(self):
        return json.loads(self.get_raw_body().decode("utf-8"))

    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get("tproxy_admin")
        if not item:
            return None
        token = item.value
        created = SESSIONS.get(token)
        if not created or time.time() - created > 7 * 24 * 3600:
            SESSIONS.pop(token, None)
            return None
        return token

    def authorized(self):
        return self.session() is not None

    def get_hostname(self):
        return PUBLIC_HOSTNAME or self.headers.get("Host", "").split(":")[0]

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/")
        if not path:
            path = "/"

        if path in ("/", "/index.html"):
            try:
                with open(SITE_HTML, "rb") as stream:
                    payload = stream.read()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND, "admin.html not found")
                return

        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Необходима авторизация", "auth": False})
            return

        if path == "/api/health":
            relay, r_code = run("systemctl", "is-active", "tproxy-server.service")
            admin, a_code = run("systemctl", "is-active", "tproxy-admin.service")
            mtproxy, m_code = run("systemctl", "is-active", "mtproxy.service")
            warp, _ = run("warp-cli", "status")
            self.send_json(200, {
                "ok": (r_code == 0 and a_code == 0 and m_code == 0),
                "relay": relay or "unknown",
                "admin": admin or "unknown",
                "mtproxy": mtproxy or "unknown",
                "warp": "connected" in warp.lower(),
                "hostname": self.get_hostname(),
                "admin_path": ADMIN_PATH,
                "time": int(time.time())
            })
        elif path == "/api/metrics":
            self.send_json(200, read_metrics())
        elif path == "/api/logs":
            query = parse_qs(url.query)
            errors_only = query.get("errors", ["0"])[0] == "1"
            self.send_json(200, read_logs(errors_only=errors_only))
        elif path == "/api/protocols":
            self.send_json(200, {"protocols": list(PROTOCOLS)})
        elif path == "/api/promo":
            self.send_json(200, {"tag": get_promo_tag()})
        elif path == "/api/site":
            cur_preset = "custom"
            marker = os.path.join(PUBLIC_DIR, ".site_preset")
            if os.path.exists(marker):
                try:
                    cur_preset = open(marker, "r", encoding="utf-8").read().strip()
                except Exception:
                    pass
            self.send_json(200, {
                "current": cur_preset,
                "current_name": PRESET_NAMES.get(cur_preset, "Пользовательский (Custom)"),
                "presets": [{"key": k, "name": v} for k, v in PRESET_NAMES.items()]
            })
        elif path == "/api/profiles":
            profiles = load_profiles_data()
            host = self.get_hostname()
            metrics_data = read_metrics()
            prof_sessions = metrics_data.get("profile_sessions", {})
            total_live = int(metrics_data.get("values", {}).get("tproxy_sessions_live", 0))

            enriched = []
            for p in profiles:
                sec = p.get("secret", "")
                name = p.get("name", "")
                limits = p.get("limits") or {}
                
                active_count = prof_sessions.get(name, 0)
                if not prof_sessions and len(profiles) == 1 and total_live > 0:
                    active_count = total_live

                enriched.append({
                    "name": name,
                    "secret": sec,
                    "carrier_mode": p.get("carrier_mode", "https"),
                    "backend": p.get("backend", "127.0.0.1:9067"),
                    "max_sessions": limits.get("max_sessions", 0),
                    "max_streams": limits.get("max_streams", 0),
                    "active_sessions": active_count,
                    "link_https": f"https://t.me/webproxy?server={host}&secret={sec}",
                    "link_tg": f"tg://webproxy?server={host}&secret={sec}"
                })
            self.send_json(200, {"profiles": enriched, "hostname": host, "total_sessions_live": total_live})
        elif path == "/api/warp":
            output, code = run("warp-cli", "status")
            self.send_json(200, {
                "installed": shutil.which("warp-cli") is not None,
                "connected": "connected" in output.lower(),
                "details": output
            })
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/")
        if not path:
            path = "/"

        if path == "/api/login":
            try:
                data = self.body_json()
                user = str(data.get("username", ""))
                pwd = str(data.get("password", ""))
                if not hmac.compare_digest(user, USERNAME) or not password_ok(pwd):
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Неверный логин или пароль"})
                    return
                token = secrets.token_urlsafe(32)
                SESSIONS[token] = time.time()
                self.send_json(200, {"ok": True, "token": token, "admin_path": ADMIN_PATH}, {
                    "Set-Cookie": f"tproxy_admin={token}; Path=/; HttpOnly; SameSite=Lax"
                })
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Сессия истекла, войдите заново", "auth": False})
            return

        if path == "/api/logout":
            token = self.session()
            if token:
                SESSIONS.pop(token, None)
            self.send_json(200, {"ok": True}, {"Set-Cookie": "tproxy_admin=; Path=/; Max-Age=0"})
        elif path == "/api/promo":
            try:
                data = self.body_json()
                tag = str(data.get("tag", "")).strip()
                saved_tag = set_promo_tag(tag)
                self.send_json(200, {"ok": True, "tag": saved_tag})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/site/preset":
            try:
                data = self.body_json()
                key = str(data.get("key", "random")).strip()
                applied = apply_site_preset(key)
                self.send_json(200, {"ok": True, "applied": applied, "name": PRESET_NAMES.get(applied, applied)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/site/upload":
            try:
                raw_zip = self.get_raw_body()
                with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
                    for member in zf.namelist():
                        if member.startswith("/") or ".." in member:
                            raise ValueError("Недопустимый путь внутри ZIP")
                    
                    for item in os.listdir(PUBLIC_DIR):
                        item_path = os.path.join(PUBLIC_DIR, item)
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)

                    zf.extractall(PUBLIC_DIR)
                
                with open(os.path.join(PUBLIC_DIR, ".site_preset"), "w", encoding="utf-8") as f:
                    f.write("custom")
                run("chmod", "-R", "a+rX", PUBLIC_DIR)
                run("systemctl", "restart", "tproxy-server.service")
                self.send_json(200, {"ok": True, "message": "Сайт успешно установлен"})
            except Exception as exc:
                self.send_json(400, {"error": f"Ошибка архива: {str(exc)}"})
        elif path == "/api/warp":
            try:
                action = self.body_json().get("action")
                if action not in ("connect", "disconnect"):
                    self.send_json(400, {"error": "Допустимы connect или disconnect"})
                    return
                output, code = run("warp-cli", action)
                self.send_json(200 if code == 0 else 500, {"ok": code == 0, "details": output})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/profiles/add":
            try:
                data = self.body_json()
                name = str(data.get("name", "")).strip()
                carrier = str(data.get("carrier_mode", "https")).strip()
                secret = str(data.get("secret", "")).strip().lower()
                backend = str(data.get("backend", "127.0.0.1:9067")).strip()
                max_sessions = int(data.get("max_sessions", 0))

                if not secret:
                    secret = "dd" + secrets.token_hex(16)

                current = load_profiles_data()
                item = {
                    "name": name,
                    "secret": secret,
                    "carrier_mode": carrier,
                    "backend": backend or "127.0.0.1:9067"
                }
                if max_sessions > 0:
                    item["limits"] = {"max_sessions": max_sessions}

                current.append(item)
                saved = save_profiles_data(current)
                self.send_json(200, {"ok": True, "profiles": saved})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/profiles/edit":
            try:
                data = self.body_json()
                orig_name = str(data.get("orig_name", "")).strip()
                name = str(data.get("name", "")).strip()
                carrier = str(data.get("carrier_mode", "https")).strip()
                secret = str(data.get("secret", "")).strip().lower()
                backend = str(data.get("backend", "127.0.0.1:9067")).strip()
                max_sessions = int(data.get("max_sessions", 0))

                current = load_profiles_data()
                found = False
                updated = []
                for p in current:
                    if p.get("name") == orig_name:
                        found = True
                        item = {
                            "name": name,
                            "secret": secret,
                            "carrier_mode": carrier,
                            "backend": backend or "127.0.0.1:9067"
                        }
                        if max_sessions > 0:
                            item["limits"] = {"max_sessions": max_sessions}
                        updated.append(item)
                    else:
                        updated.append(p)

                if not found:
                    raise ValueError(f"Профиль {orig_name} не найден")

                saved = save_profiles_data(updated)
                self.send_json(200, {"ok": True, "profiles": saved})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/profiles/delete":
            try:
                data = self.body_json()
                name = str(data.get("name", "")).strip()
                current = load_profiles_data()
                filtered = [p for p in current if p.get("name") != name]
                if len(filtered) == len(current):
                    raise ValueError(f"Профиль {name} не найден")
                if not filtered:
                    raise ValueError("Нельзя удалить единственный профиль")
                saved = save_profiles_data(filtered)
                self.send_json(200, {"ok": True, "profiles": saved})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        elif path == "/api/profiles":
            try:
                data = self.body_json()
                profiles = data.get("profiles", [])
                saved = save_profiles_data(profiles)
                self.send_json(200, {"ok": True, "profiles": saved})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 9090), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()