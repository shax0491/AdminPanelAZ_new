# AmneziaWG 3.0 в панели

Интерфейс `awg1` на ноде (UDP 51821, MTU 1280). Устанавливается `setup.sh` базового репозитория (раздел «AmneziaWG 3.0» в README там же).

## Что делает панель
- Вкладка `/awg3`: состояние инструментов (`awg`, `amneziawg-go`), интерфейсы, список клиентов с режимом, handshake.
- Создание клиента: имя (1–32 символа: латиница, цифры, `_`, `-`) и режим.
  - `split` — антизапрет: `10.9.0.0/24`, DNS `10.9.0.1`, `AllowedIPs` = подсеть сервера + список маршрутов антизапрета.
  - `full` — полный VPN: `10.9.1.0/24`, DNS `10.9.1.1`, `AllowedIPs = 0.0.0.0/0`.
- Скачивание `.conf` (параметры обфускации берутся с сервера), удаление клиента (пир снимается с интерфейса и из конфига).
- Telegram-бот: `/awg3` (только админ, при включённом фичефлаге).

## API
- `GET /api/awg3/health`, `GET /api/awg3/monitoring`
- `GET /api/awg3/clients`, `POST /api/awg3/clients` с телом `{"name": "...", "mode": "split" | "full"}`
- `GET /api/awg3/clients/{name}/config` (JSON с полем `config`), `DELETE /api/awg3/clients/{name}`

Все вызовы идут через адаптер активной ноды: локальный адаптер (нода = сервер панели) или удалённый — запросом к агенту ноды.

## Агент ноды
Эндпоинты `/awg3/*` в `backend/node_agent/main.py` требуют на ноде:
- `AWG3_ENDPOINT_HOST` — адрес, который попадает в клиентские конфиги (например, `de2.example.com`), в `node_agent.env`;
- `AWG3_SPLIT_ALLOWED_FILE` — список маршрутов антизапрета (по умолчанию `/etc/amnezia/amneziawg3/split-allowed.txt`).

## Хранение
- Клиенты, их ключи и PSK: `/etc/amnezia/amneziawg3/clients.json` на ноде, права 600. Пиры дублируются в `awg1.conf`, чтобы сохраняться после перезапуска `awg3@awg1`.
- Ключи в БД панели не хранятся.

## Ограничения
- Клиентские приложения AmneziaWG 2.0 не принимают конфиги 3.x. Сейчас это роутеры (OpenWrt с `amneziawg` 3.x, KeeneticOS 5.2+).
- Фиктивные адреса антизапрета (198.18/15) работают только через туннель и только для доменов из списка антизапрета.

## Проверка
- `pytest tests/test_awg3_clients.py tests/test_awg3_panel_layer.py`
- На ноде: `awg show awg1`, `iptables -t nat -S PREROUTING | grep 10.9.`, handshake клиента через `awg show awg1 latest-handshakes`.
