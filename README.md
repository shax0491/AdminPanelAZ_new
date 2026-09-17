# 🛡️ AdminPanel AntiZapret

Веб-панель для администрирования VPN-сервера [AntiZapret](https://github.com/shax0491/AntiZapret-VPN_new): клиенты, маршрутизация, мониторинг, бэкапы, Telegram.

[![GitHub](https://img.shields.io/badge/GitHub-shax0491%2FAdminPanelAZ__new-181717?style=for-the-badge&logo=github)](https://github.com/shax0491/AdminPanelAZ_new)
[![Version](https://img.shields.io/badge/Панель-2.25.0-blue?style=for-the-badge)](CHANGELOG.md)
[![Node agent](https://img.shields.io/badge/Node_agent-1.8.0-555?style=for-the-badge)](CHANGELOG.md)

[🚀 Установка](#-быстрый-старт) · [✨ Возможности](#-возможности) · [📖 Все инструкции](docs/README.md) · [💬 Пожелания и баги](https://claymore0098.fider.io/)

<p align="center">
  <img src="docs/assets/telegram-promo/01-hero-banner.png" alt="AdminPanel AntiZapret" width="900">
</p>

> AntiZapret ставится **отдельно**, на сам VPN-сервер — см. [инструкцию AntiZapret-VPN](https://github.com/shax0491/AntiZapret-VPN_new). Эта панель только управляет им (или несколькими такими серверами) через веб-интерфейс.

---

## 🚀 Быстрый старт

**Нужно:** Ubuntu 24.04+ или Debian 13+, root/sudo, интернет.
Если недавно делали `apt upgrade` (обновилось ядро) — сначала **перезагрузите сервер**, потом ставьте панель.

```bash
sudo apt update && sudo apt install -y git wget curl
curl -fsSL https://raw.githubusercontent.com/shax0491/AdminPanelAZ_new/refs/heads/main/install.sh | sudo bash
```

Установщик задаст несколько вопросов: что ставим, порты, логин/пароль администратора, каталог бэкапов. После установки панель сразу открывается по `http://IP:порт/` (обычно **8000**).

### Что именно ставить

Первый вопрос мастера — какую роль играет этот сервер:

| Вариант | Что ставится | Когда выбирать |
| --- | --- | --- |
| **Только панель** | Веб-интерфейс | Отдельный управляющий сервер; VPN-узлы подключаются к нему извне |
| **Панель + узел** | Панель и агент на одном хосте | AntiZapret уже стоит на этом же сервере — «всё на одном VDS» |
| **Узел** | Только node agent | VPN-сервер, который нужно подключить к панели на другом хосте |
| **Прокси** | Только proxy_agent | RU VPS с уже установленным `proxy.sh` — агент даёт мониторинг и смену адреса из панели ([инструкция](docs/proxy-agent.md)) |

### Порты

| Порт | Для чего | Кому открывать |
| --- | --- | --- |
| **8000** (или свой) | Панель | LAN/интернет |
| **9100** | Node agent | Между панелью и узлом (localhost при SSH-транспорте) |
| **80 / 443** | HTTPS | После настройки домена в панели |

Порты OpenVPN/WireGuard/AmneziaWG задаёт сам **AntiZapret**, не панель. Подробнее об открытии портов — [SECURITY.md](SECURITY.md).

### ✅ После установки — что сделать первым делом

1. Открыть адрес из вывода установщика и войти. Логин по умолчанию, если не задавали в мастере: `admin` / `admin` — **сразу смените**.
2. Включить **2FA** — [Настройки → Профиль](docs/nastrojki/profil.md).
3. Перевести панель на **HTTPS** — [Настройки → Адрес сайта и HTTPS](docs/nastrojki/set-i-publikaciya.md) (свой домен или бесплатный DDNS, см. ниже).
4. Если VPN на другом сервере — добавить узел: [docs/uzly.md](docs/uzly.md).
5. На вкладке **Конфигурации** нажать **Синхронизировать**.
6. Указать Telegram bot token, если нужен бот/уведомления — [docs/Telegram.md](docs/Telegram.md).

Авто-бэкап включается после установки сам (раз в 7 дней) — [настройка](docs/nastrojki/rezervnye-kopii.md).

---

## ✨ Возможности

<p align="center">
  <img src="docs/assets/telegram-promo/02-features-overview.png" alt="Все модули AdminPanel AntiZapret" width="900">
</p>

**VPN и клиенты** — OpenVPN, WireGuard, AmneziaWG: создание, QR-коды, блокировка, лимиты трафика ([инструкция](docs/konfiguracii.md)). Несколько серверов из одной панели ([узлы](docs/uzly.md)), отказоустойчивость HA ([Node Sync](docs/NodeSync.md)), автопереключение между узлами без переустановки клиента ([автопереключение](docs/autoswitch-howto.html)). Подписка с unlock-кодами и постоянные ссылки для клиентов — раздел **Подписка** ([инструкция](docs/podpiska.md)).

**Маршрутизация** — списки провайдеров (CIDR), пресеты, редактор конфигов AntiZapret с применением на сервер ([маршрутизация](docs/routing-cidr.md), [конфиг](docs/antizapret-config.md)), точечная маршрутизация через Cloudflare WARP ([AZ-WARP](docs/warper.md)).

**Мониторинг** — NOC: кто подключён, откуда, графики, состояние служб, сводки в Telegram ([инструкция](docs/noc-monitoring.md)); расход трафика по клиентам ([трафик](docs/traffic-monitoring.md)); живые CPU/RAM/диск и история за 30 дней ([сервер](docs/server-monitor.md)).

**Безопасность** — роли администратор/пользователь, 2FA, белый список IP, защита от перебора паролей ([безопасность](docs/nastrojki/bezopasnost.md)), бэкапы вручную и по расписанию с отправкой в Telegram.

**Telegram** — вход в панель (Login Widget или OpenID Connect), Mini App с конфигами, бот с командами, уведомления нескольким получателям ([инструкция](docs/Telegram.md)).

Полный список инструкций по каждому разделу: **[docs/README.md](docs/README.md)**.

---

## 🌐 Бесплатный адрес для панели (DDNS)

Если нет своего домена: **Настройки → Адрес сайта и HTTPS → Динамический DNS**.

- **[DuckDNS](https://www.duckdns.org/)** — создайте два имени (одно для AntiZapret, другое для панели), укажите панельное + token. VPN-имя DuckDNS как домен панели использовать нельзя — [подробнее](docs/nastrojki/set-i-publikaciya.md#duckdns-duckdnsorg).
- **[No-IP](https://www.noip.com)** или свой домен — на один hostname можно повесить несколько A-записей на разные IP.

Автообновление IP работает через systemd-таймер каждые 5 минут; вручную — `sudo ./scripts/ddns-update.sh update|status`.

## 🔗 Если на этом же домене уже стоит StatusOpenVPN

[StatusOpenVPN](https://github.com/TheMurmabis/StatusOpenVPN) занимает `https://домен/status/` — если поставить оба сайта на nginx независимо, будет конфликт.

Ставьте панель по HTTP, затем в **Настройки → Адрес сайта и HTTPS** включите Nginx + Let's Encrypt с подпутём `panel` и опцией **Интегрировать с StatusOpenVPN**. Получится `https://домен/status/` и `https://домен/panel/`. Не удаляйте StatusOpenVPN через его `uninstall` после интеграции — сломает nginx; если панель пропала — `sudo ./scripts/nginx-repair.sh`. Подробнее: [docs/nastrojki/set-i-publikaciya.md](docs/nastrojki/set-i-publikaciya.md#совместно-со-statusopenvpn-на-одном-домене).

## ⚙️ Продакшен: ресурсы и Redis

Ориентир по памяти (панель + локальный узел, профиль Full): ~400 МБ. Для одного VDS с VPN хватает 1-2 ГБ; только панель — 1 ГБ + swap.

При нескольких workers (`UVICORN_WORKERS > 1` в `backend/.env`) нужен Redis: `AUTH_RATE_LIMIT_BACKEND=redis`, `API_RATE_LIMIT_BACKEND=redis`, `REDIS_URL=redis://127.0.0.1:6379/0` — подробнее в [SECURITY.md](SECURITY.md). Модули и профиль меняются в **Настройки → Модули** ([инструкция](docs/nastrojki/moduli.md)).

Для приватных IP узлов (LAN) — `ALLOW_INTERNAL_NODES=true` в `.env`; mTLS/SSH настраиваются per-node в **Узлах** — [docs/uzly.md](docs/uzly.md) · [SSH-транспорт](docs/node-ssh-transport.md).

- Health: `GET /api/health`, `GET /api/health/deep`
- Метрики: `GET /metrics` (Prometheus)

## 🗑️ Удаление и переустановка

```bash
sudo ./install.sh              # меню: переустановка или удаление
sudo ./install.sh --uninstall  # удалить только сервисы панели
```

AntiZapret и VPN-конфиги при удалении панели **не трогаются**.

## 💻 Полезные команды на сервере

```bash
cd /opt/AdminPanelAZ
sudo ./scripts/adminpanel-menu.sh          # меню: перезапуск, бэкап, обновление
sudo systemctl restart adminpanelaz        # перезапуск панели
sudo systemctl restart adminpanelaz-proxy  # proxy_agent на RU (порт 9101)
sudo ./scripts/nginx-setup.sh              # сменить HTTPS после установки
sudo ./scripts/nginx-repair.sh             # восстановить nginx
```

## 📖 Дальше

- **Пользователям и администраторам** — [docs/README.md](docs/README.md), инструкция по каждому разделу
- **Разработчикам** — [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [docs/PROJECT_MAP.md](docs/PROJECT_MAP.md)
- **История изменений** — [CHANGELOG.md](CHANGELOG.md), текущая версия панель **2.25.0** / node agent **1.8.0**
- **Предыдущая версия на Flask** — [AdminAntizapret](https://github.com/Kirito0098/AdminAntizapret) (архив)

## 💬 Обратная связь и поддержка

Пожелания и баги — доска **[AdminPanelAZ на Fider](https://claymore0098.fider.io/)** (сначала поищите — вдруг тема уже есть).

Донат: [cloudtips.ru](https://pay.cloudtips.ru/p/3c6704ca) · Telegram-группа: [ссылка](https://t.me/+XJwXHTmMvUk3NTli) · Личные сообщения: [@Claymore0098](https://t.me/Claymore0098)

---

*Сделано с ❤️ для сообщества AntiZapret · [⭐ Star на GitHub](https://github.com/shax0491/AdminPanelAZ_new)*
