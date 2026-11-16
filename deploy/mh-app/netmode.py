"""Режим «Только IPv4» для Mihomo: dns.ipv6=false в загружаемом конфиге и горячая загрузка payload.

PATCH /configs {"ipv6": false} AAAA не подавляет, а PUT с path на практике давал 400, поэтому
конфиг передаётся целиком: PUT /configs?force=true {"payload": "<yaml>"}, затем сброс DNS-кэша
(иначе клиент ещё какое-то время получает старые AAAA). Выбранные карточки при этом сохраняются.
"""
import json
import os
import re
import urllib.request

_DNS_IPV6 = re.compile(r"(?ms)^(dns:\n.*?^  ipv6: )(true|false)$")


def mode_of(card_now):
    """Выбранный пункт карточки «Сетевой режим» -> prio | ipv4 | other."""
    if card_now.startswith("⚡"):
        return "prio"
    return "ipv4" if card_now.startswith("🛡") else "other"


def set_dns_ipv6(text, enabled):
    """Меняет только dns.ipv6 (не верхнеуровневый ipv6) - идемпотентно."""
    new, n = _DNS_IPV6.subn(lambda m: m.group(1) + ("true" if enabled else "false"), text, count=1)
    if n != 1:
        raise ValueError("в конфиге нет dns.ipv6")
    return new


def _call(api, secret, path, method, body=None, timeout=30):
    req = urllib.request.Request(
        api + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def load_payload(api, secret, text, timeout=30):
    _call(api, secret, "/configs?force=true", "PUT", {"payload": text}, timeout)
    _call(api, secret, "/cache/dns/flush", "POST", None, timeout)


def apply_config(api, secret, new_text, old_text, timeout=30):
    """Загружает new_text; при ошибке возвращает old_text. -> "applied" | "restored".
    Если не удался и откат, бросает RuntimeError (роутер остаётся в неизвестном состоянии - нужен ручной разбор)."""
    try:
        load_payload(api, secret, new_text, timeout)
        return "applied"
    except Exception as first:
        try:
            load_payload(api, secret, old_text, timeout)
        except Exception as second:
            raise RuntimeError("загрузка нового конфига не удалась (%s) и откат тоже (%s)" % (first, second))
        return "restored"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write_atomic(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def follow_once(last, cur, ipmode_file, home_config, refresh, rebuild, api, secret, log):
    """Одна итерация mode_follow: карточка перешла в режим cur. Возвращает новое значение last.

    Любая ошибка не повторяется на следующем тике (иначе пересборка и PUT каждые 2 с): ipmode.state и HOME_CONFIG
    возвращаются к прежним, ошибка пишется в журнал, last = cur - следующая попытка только при новой смене карточки."""
    if cur == last:
        return last
    prev = _read(ipmode_file).strip() or "other"
    if last is None and prev == cur:
        return cur
    old_cfg = _read(home_config)
    _write_atomic(ipmode_file, cur)
    try:
        refresh()
        if "ipv4" in (prev, cur):  # конфиг пересобирается с новым dns.ipv6 и грузится payload-ом
            rebuild()
            new_cfg = _read(home_config)
            if not old_cfg.strip():  # прежнего конфига нет: пустой payload «откатом» не грузим, ошибка загрузки уйдёт в except
                load_payload(api, secret, new_cfg)
            elif apply_config(api, secret, new_cfg, old_cfg) == "restored":
                _write_atomic(home_config, old_cfg)
                _write_atomic(ipmode_file, prev)
                log("mode_follow: режим «%s» НЕ применён: Mihomo не принял новый payload; восстановлены прежний конфиг (Mihomo и %s) и режим «%s»" % (cur, home_config, prev))
    except Exception as e:  # noqa: BLE001 - любая ошибка сборки/загрузки
        _write_atomic(ipmode_file, prev)
        if old_cfg.strip() and _read(home_config) != old_cfg:
            _write_atomic(home_config, old_cfg)
        log("mode_follow: режим «%s» НЕ применён: %s; ipmode.state возвращён в «%s», повтор только после новой смены карточки" % (cur, e, prev))
    return cur
