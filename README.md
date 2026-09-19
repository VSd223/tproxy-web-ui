# 🛡 TProxy Web UI (Stealth Telegram Proxy)

![Version](https://img.shields.io/badge/Version-4.1%20Stable-blue?style=for-the-badge&logo=rocket)
![OS](https://img.shields.io/badge/OS-Ubuntu%20%7C%20Debian-green?style=for-the-badge&logo=linux)
![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge)
![Telegram](https://img.shields.io/badge/Protocol-WEB--Proxy-2CA5E0?style=for-the-badge&logo=telegram)

Умный автоинсталлятор скрытного WEB-прокси для Telegram на базе [официального tproxy-server](https://github.com/telegramdesktop/tproxy-server). Включает в себя удобную Web-панель управления, защиту от активного зондирования (DPI) и автоматическую маскировку под полноценные веб-сайты.

## ✨ Главные возможности
* **Web-панель управления:** Удобный интерфейс для добавления/удаления устройств, генерации QR-кодов и ссылок.
* **Максимальная скрытность:** Трафик маскируется под обычный HTTPS (TLS 1.3) или WebSocket (HTTP/2, HTTP/3).
* **Защита от сканеров (Anti-DPI):** Секретный, случайно генерируемый URL для входа в панель. Никаких торчащих портов MTProto.
* **Сайт-маскировка:** При переходе по вашему домену в браузере открывается красивый сайт (на выбор 10 шаблонов или загрузка своего ZIP-архива).
* **Спонсорский канал:** Поддержка официального промо-тега от `@MTProxybot`.
* **Полная персистентность:** Настройки, профили и лимиты сохраняются даже после жесткой перезагрузки сервера.
* **Авто-SSL:** Автоматический выпуск сертификатов Let's Encrypt через Nginx или Caddy.

## 📋 Системные требования
* **ОС:** `Ubuntu 20.04 / 22.04 / 24.04` или `Debian 11 / 12`
* **Домен:** Привязанный к IP-адресу вашего сервера (A-запись).
* **Права:** `root` (суперпользователь).
* **Порты:** Должны быть открыты порты `80` и `443` (TCP/UDP).

---

## 🚀 Установка

> ⚠️ **Рекомендация:** Устанавливайте скрипт на чистый сервер (Clean OS), чтобы избежать конфликтов с портами 80/443 и уже установленными веб-серверами.

Подключитесь к вашему серверу по SSH и выполните следующие команды по очереди:

```bash
git clone https://github.com/VSd223/tproxy-web-ui.git
cd tproxy-web-ui
sudo bash install.sh
