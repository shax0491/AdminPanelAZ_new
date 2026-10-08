"""MTProxy (MTProxyL) на узлах: состояние прокси и доступность из России для оповещений бота панели.

На узле (агент или локальный узел) читается то, что MTProxyL сам знает о себе:
- `mtproxyl status --json` - работает ли прокси, версия, порт, домен FakeTLS, подключения;
- /opt/mtproxyl/availability/history.jsonl - результаты проверок доступности из России
  (MTProxyL проверяет каждые 15 минут зондами из российских сетей).

Панель опрашивает узлы не чаще раза в CACHE_SECONDS (правила оповещений вызываются каждую минуту),
и узлы без MTProxyL или со старым агентом просто не участвуют в метриках.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MTPROXYL_DIR = Path("/opt/mtproxyl")
MTPROXYL_SCRIPT = MTPROXYL_DIR / "mtproxyl.sh"
AVAILABILITY_HISTORY = MTPROXYL_DIR / "availability" / "history.jsonl"
EXPERT_CONF = MTPROXYL_DIR / "expert.conf"
RECENT_CHECKS = 8
CACHE_SECONDS = 120


def _mtproxyl_cmd() -> list[str] | None:
    found = shutil.which("mtproxyl")
    if found:
        return [found]
    if MTPROXYL_SCRIPT.is_file():
        return ["bash", str(MTPROXYL_SCRIPT)]
    return None


def parse_status_json(text: str) -> dict | None:
    """Последний JSON-объект в выводе `mtproxyl status --json` (перед ним бывают строки лога)."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            return data if isinstance(data, dict) else None
    return None


def parse_last_json(text: str):
    """Последний JSON (объект или массив) в выводе mtproxyl: перед ним бывают строки лога."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def parse_public_port(text: str) -> int | None:
    """Порт в ссылках tg://proxy из expert.conf (general.links|public_port|443); None - как слушает прокси."""
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0] == "general.links" and parts[1] == "public_port" and parts[2].isdigit():
            return int(parts[2])
    return None


def merge_users(traffic: dict | None, secrets: list | None) -> list[dict]:
    """Пользователи прокси: трафик из `mtproxyl traffic --json` + лимиты из `secret list --json`.

    Сам секрет и история IP не передаются. Квоту движок считает с последнего запуска прокси, поэтому
    расход квоты - трафик текущей сессии, а не за всё время.
    """
    limits = {str(item.get("label")): item for item in secrets or [] if isinstance(item, dict)}
    users: list[dict] = []
    for item in (traffic or {}).get("users") or []:
        if not isinstance(item, dict) or item.get("deleted"):
            continue
        label = str(item.get("user") or "")
        limit = limits.get(label, {})
        session = int(item.get("session_in") or 0) + int(item.get("session_out") or 0)
        quota = int(limit.get("quota_bytes") or 0)
        users.append({
            "label": label,
            "enabled": bool(item.get("enabled", limit.get("enabled", True))),
            "connections": int(item.get("connections") or 0),
            "unique_ips": int(item.get("unique_ips") or 0),
            "total_bytes": int(item.get("total") or 0),
            "session_bytes": session,
            "max_conns": int(limit.get("max_conns") or 0),
            "max_ips": int(limit.get("max_ips") or 0),
            "quota_bytes": quota,
            "quota_pct": round(session * 100 / quota, 1) if quota > 0 else None,
            "expires": str(limit.get("expires") or "0"),
        })
    return users


def parse_availability_history(text: str, limit: int = RECENT_CHECKS) -> list[dict]:
    """Последние проверки доступности (новые в конце): {checked_at, percentage, success, total, target, error}."""
    checks: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        percentage = item.get("percentage")
        checks.append({
            "checked_at": item.get("checked_at"),
            "percentage": float(percentage) if isinstance(percentage, (int, float)) else None,
            "success": item.get("success_probes"),
            "total": item.get("total_probes"),
            "target": item.get("target") or "",
            "error": item.get("error") or "",
        })
    return checks[-limit:]


def collect_mtproxy_status() -> dict:
    """Состояние MTProxyL на этом сервере (выполняется на узле от root)."""
    cmd = _mtproxyl_cmd()
    if cmd is None:
        return {"installed": False}
    result: dict = {"installed": True, "running": False, "status": "unknown", "error": None}
    try:
        proc = subprocess.run(
            [*cmd, "status", "--json"], capture_output=True, text=True, timeout=30,
            env={**os.environ, "MTPROXYL_NONINTERACTIVE": "true"},
        )
        data = parse_status_json(proc.stdout)
        if data is None:
            result["error"] = (proc.stderr or proc.stdout or "mtproxyl status --json не вернул JSON").strip()[-300:]
        else:
            result.update({
                "status": str(data.get("status") or "unknown"),
                "running": data.get("status") == "running",
                "version": data.get("version"),
                "port": data.get("port"),
                "domain": data.get("domain"),
                "connections": data.get("connections"),
                "unique_ips": data.get("unique_ips"),
            })
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["error"] = f"mtproxyl status: {exc}"

    try:
        result["public_port"] = parse_public_port(EXPERT_CONF.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        result["public_port"] = None
    result["users"] = _collect_users(cmd)

    history_text = ""
    try:
        history_text = AVAILABILITY_HISTORY.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    recent = parse_availability_history(history_text)
    result["availability"] = recent[-1] if recent else None
    result["availability_recent"] = recent
    result["checked_at"] = int(time.time())
    return result


def _collect_users(cmd: list[str]) -> list[dict]:
    env = {**os.environ, "MTPROXYL_NONINTERACTIVE": "true", "NO_COLOR": "1"}
    outputs = []
    for args in (["traffic", "--json"], ["secret", "list", "--json"]):
        try:
            proc = subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=30, env=env)
            outputs.append(parse_last_json(proc.stdout))
        except (subprocess.TimeoutExpired, OSError):
            outputs.append(None)
    traffic, secrets = outputs
    return merge_users(traffic if isinstance(traffic, dict) else None, secrets if isinstance(secrets, list) else None)


# --- панель: кэш опроса узлов -------------------------------------------------------------------

_cache: dict[int, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def node_mtproxy_status(node, *, max_age: float = CACHE_SECONDS) -> dict | None:
    """Состояние MTProxy узла из кэша или с агента. None - узел не ответил или агент не умеет."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(node.id)
        if cached and now - cached[0] < max_age:
            return cached[1]
    from app.services.node_manager import get_adapter_for_node

    try:
        data = get_adapter_for_node(node).get_mtproxy_status()
    except Exception as exc:  # агент недоступен или старый (нет /mtproxy/status)
        logger.debug("MTProxy status of node %s unavailable: %s", node.id, exc)
        data = None
    with _cache_lock:
        _cache[node.id] = (now, data)
    return data


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _monitored_nodes(db, node_id: int | None):
    from app.models import Node, NodeStatus

    query = db.query(Node).filter(Node.status == NodeStatus.online)
    if node_id is not None:
        query = query.filter(Node.id == node_id)
    return query.order_by(Node.id.asc()).all()


def mtproxy_down_value(db, node_id: int | None) -> float | None:
    """Сколько узлов с MTProxyL, где прокси не работает (для одного узла: 1 или 0)."""
    statuses = [node_mtproxy_status(node) for node in _monitored_nodes(db, node_id)]
    installed = [s for s in statuses if s and s.get("installed")]
    if not installed:
        return None
    # "unknown" - mtproxyl не ответил JSON: не считаем остановкой, чтобы не будить ложной тревогой
    return float(sum(1 for s in installed if not s.get("running") and s.get("status") != "unknown"))


def mtproxy_availability_value(db, node_id: int | None) -> float | None:
    """Доступность из России по последней проверке, %; по всем узлам - худший узел."""
    values: list[float] = []
    for node in _monitored_nodes(db, node_id):
        status = node_mtproxy_status(node)
        availability = (status or {}).get("availability") or {}
        if status and status.get("installed") and availability.get("percentage") is not None:
            values.append(float(availability["percentage"]))
    return min(values) if values else None


def mtproxy_quota_max_value(db, node_id: int | None) -> float | None:
    """Наибольший расход квоты среди пользователей с квотой, % (100 и больше - квота исчерпана)."""
    values = [
        float(user["quota_pct"])
        for node in _monitored_nodes(db, node_id)
        for user in ((node_mtproxy_status(node) or {}).get("users") or [])
        if user.get("quota_pct") is not None
    ]
    return max(values) if values else None


def mtproxy_quota_users(db, node_id: int | None, min_pct: float) -> list[str]:
    """Строки «узел: пользователь — N% квоты» для текста оповещения."""
    lines: list[str] = []
    for node in _monitored_nodes(db, node_id):
        for user in (node_mtproxy_status(node) or {}).get("users") or []:
            pct = user.get("quota_pct")
            if pct is not None and pct >= min_pct:
                lines.append(f"{node.name}: {user['label']} — {pct:g}% квоты")
    return lines


def mtproxy_overview(db) -> list[dict]:
    """Сводка по узлам с MTProxyL для страницы мониторинга и бота."""
    rows: list[dict] = []
    for node in _monitored_nodes(db, None):
        status = node_mtproxy_status(node)
        if not status or not status.get("installed"):
            continue
        rows.append({"node_id": node.id, "node_name": node.name, **status})
    return rows


def mtproxy_all_nodes(db) -> list[dict]:
    """Все VPN-узлы для вкладки MTProxy панели: и без MTProxyL, и недоступные, чтобы было видно, где его нет."""
    from app.models import Node, NodeStatus
    from app.services.node_manager import is_vpn_node

    rows: list[dict] = []
    for node in db.query(Node).order_by(Node.id.asc()).all():
        if not is_vpn_node(node):
            continue
        row: dict = {"node_id": node.id, "node_name": node.name, "node_online": node.status == NodeStatus.online}
        status = node_mtproxy_status(node) if row["node_online"] else None
        if status is None:
            row["installed"] = None  # агент не ответил или не умеет /mtproxy/status
        else:
            row.update(status)
        rows.append(row)
    return rows
