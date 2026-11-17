"""Уведомления Mini App mh-app по-русски: Hysteria2 доступен/недоступен, смена транспорта и резерва, режим сети, ошибки.

Чистая логика без сети: `Watcher.feed()` получает снимок (живость узлов, выбранные выходы, режим) и возвращает список текстов.
Недоступность пути проверки (нет связи с роутером) не выдаётся за падение серверов. Дребезг: «недоступен» - только после
2 снимков подряд, «восстановлен» - сразу после уведомления о падении; смена выхода «Авто» - не чаще раза в COOLDOWN секунд,
но возврат с резерва шлётся всегда.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

RESERVE_DEFAULT = ("FI1", "FR1", "LV1")
COOLDOWN = 600
DOWN_AFTER = 2
LINK_DOWN_AFTER = 3


def parse_leaf(name: str) -> tuple[str, str]:
    """'🇩🇪 DE4 | Hysteria2' -> ('DE4', 'Hysteria2'); незнакомое имя -> (имя, '')."""
    m = re.match(r"^\S*\s*([A-Z]{2}\d)\s*\|\s*(.+)$", name or "")
    return (m.group(1), m.group(2).strip()) if m else (name or "", "")


def tag(node: str) -> str:
    """'DE4' -> '🇩🇪 DE4' (флаг страны по первым двум буквам ключа узла)."""
    if len(node) >= 2 and node[:2].isalpha() and node[:2].isupper():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in node[:2]) + " " + node
    return node


def human(name: str) -> str:
    node, proto = parse_leaf(name)
    return f"{tag(node)}, {proto}" if proto else (name or "—")


def stamp(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), timezone(timedelta(hours=3))).strftime("%H:%M")  # МСК = UTC+3 без перехода


class Watcher:
    def __init__(self, reserve=RESERVE_DEFAULT, clock=time.time):
        self.reserve = tuple(reserve)
        self.clock = clock
        self.down: dict[str, int] = {}       # узел Hysteria2 -> сколько снимков подряд «не жив»
        self.announced_down: set[str] = set()
        self.auto_last: dict[str, str] = {}
        self.auto_sent: dict[str, float] = {}
        self.mode_last: str | None = None
        self.link_fail = 0
        self.link_announced = False

    # --- путь проверки ---
    def link(self, ok: bool) -> list[str]:
        out = []
        if ok:
            if self.link_announced:
                out.append(f"✅ Связь с роутером восстановлена\nПуть проверки снова работает, состояние узлов обновляется.\nВремя: {stamp(self.clock())} МСК")
            self.link_fail, self.link_announced = 0, False
        else:
            self.link_fail += 1
            if self.link_fail >= LINK_DOWN_AFTER and not self.link_announced:
                self.link_announced = True
                out.append(f"⚠️ Нет связи с роутером\nНе удаётся прочитать состояние Mihomo через туннель управления. Это не значит, что серверы упали: "
                           f"состояние узлов временно неизвестно.\nЧто делать: ничего, если роутер выключен; иначе проверить интернет дома.\nВремя: {stamp(self.clock())} МСК")
        return out

    # --- Hysteria2 ---
    def hy2(self, alive: dict[str, bool]) -> list[str]:
        out = []
        for name, ok in alive.items():
            node, _ = parse_leaf(name)
            if ok:
                self.down[node] = 0
                if node in self.announced_down:
                    self.announced_down.discard(node)
                    out.append(f"✅ Hysteria2 восстановлен\nУзел: {tag(node)}\nБыло: недоступен → стало: доступен\nВремя: {stamp(self.clock())} МСК")
            else:
                self.down[node] = self.down.get(node, 0) + 1
                if self.down[node] >= DOWN_AFTER and node not in self.announced_down:
                    self.announced_down.add(node)
                    out.append(f"⚠️ Hysteria2 недоступен\nУзел: {tag(node)}\nБыло: доступен → стало: недоступен (проверка не прошла {self.down[node]} раза подряд)\n"
                               f"Что делать: ничего — трафик идёт по VLESS этого узла или другому выходу; если не восстановится, проверьте порт UDP узла.\nВремя: {stamp(self.clock())} МСК")
        return out

    # --- выбранный выход автогруппы (транспорт / резерв) ---
    def auto(self, group: str, leaf: str) -> list[str]:
        prev, self.auto_last[group] = self.auto_last.get(group), leaf
        if not prev or prev == leaf:
            return []
        (n0, p0), (n1, p1) = parse_leaf(prev), parse_leaf(leaf)
        was_res, is_res = n0 in self.reserve, n1 in self.reserve
        if (n0, p0) == (n1, p1):
            return []
        now = self.clock()
        back = was_res and not is_res
        if p0 == p1 and was_res == is_res:     # просто другой узел того же транспорта - не событие
            return []
        if not back and now - self.auto_sent.get(group, 0) < COOLDOWN:
            return []
        self.auto_sent[group] = now
        if is_res and not was_res:
            head, hint = "🛟 Включился резерв", "Основные узлы медленнее или недоступны, трафик ушёл на запасной."
        elif back:
            head, hint = "↩️ Возврат с резерва", "Основной выход снова лучший."
        elif p1 == "Hysteria2":
            head, hint = "🔀 Переключение на Hysteria2", "Hysteria2 сейчас быстрее."
        elif p0 == "Hysteria2":
            head, hint = "🔀 Переключение с Hysteria2 на VLESS", "VLESS сейчас быстрее или Hysteria2 хуже отвечает."
        else:
            head, hint = "🔀 Смена транспорта", "Автовыбор нашёл более быстрый вариант."
        return [f"{head}\nГруппа: «{group}»\nБыло: {human(prev)} → стало: {human(leaf)}\n{hint}\nВремя: {stamp(now)} МСК"]

    # --- сетевой режим ---
    def mode(self, now_mode: str) -> list[str]:
        prev, self.mode_last = self.mode_last, now_mode
        if prev is None or prev == now_mode:
            return []
        return [f"🌐 Сетевой режим изменён\nБыло: {prev} → стало: {now_mode}\nВремя: {stamp(self.clock())} МСК"]


def apply_error(what: str, old: str, new: str, reason: str, clock=time.time) -> str:
    """Понятная ошибка без traceback: что не вышло, какой выбор остался."""
    if re.search(r"Command|returned non-zero|Traceback|Errno", reason):
        reason = "не удалось пересобрать конфигурацию на сервере"
    reason = reason.strip().splitlines()[0][:160] if reason.strip() else "причина не определена"
    return (f"❌ Не удалось применить: {what}\nХотели: {new}. Остался прежний выбор: {old}.\nПричина: {reason}\n"
            f"Что делать: подождите минуту и повторите; если повторится — сообщите администратору.\nВремя: {stamp(clock())} МСК")
