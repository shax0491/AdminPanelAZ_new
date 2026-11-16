from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Node, ReverseTunnelLink
from app.services.node_manager import get_adapter_for_node, get_proxy_adapter
from app.services.reverse_tunnel import pick_direct_ipv4

TRANSPORT_MODES = {"auto", "force_ipv6", "force_ipv4", "reverse"}
EGRESS_MODES = {"native", "proton", "warp", "ipv4_only", "ipv6_prefer"}
PRODUCTION_MH_NODES_FILE = Path("/opt/mh-app/nodes.json")
MH_NODES_FILE = Path(os.environ.get("MH_NODES_FILE", str(PRODUCTION_MH_NODES_FILE)))
MH_REGISTRY_SYNC_TEST_MODE = os.environ.get("MH_REGISTRY_SYNC_TEST_MODE", "").lower() in {"1", "true", "yes"}


def _sync_mh_registry(node: Node) -> None:
    if MH_REGISTRY_SYNC_TEST_MODE and MH_NODES_FILE == PRODUCTION_MH_NODES_FILE:
        raise ValueError("тестовый режим sync не может использовать production registry")
    if node.node_kind != "vpn" or not MH_NODES_FILE.is_file():
        return
    if not MH_REGISTRY_SYNC_TEST_MODE:
        addresses = [(node.ipv4_address or node.host, 4, "IPv4")]
        if node.has_ipv6 and node.ipv6_address:
            addresses.append((node.ipv6_address, 6, "IPv6"))
        for value, family, label in addresses:
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError as exc:
                raise ValueError(f"{label} адрес некорректен для production registry") from exc
            if address.version != family or not address.is_global:
                raise ValueError(f"{label} тестовый/documentation/non-global адрес запрещён в production registry")
    data = json.loads(MH_NODES_FILE.read_text(encoding="utf-8"))
    changed = False
    for item in data.get("nodes", []):
        if item.get("key") != node.name:
            continue
        item["ipv4_address"] = node.ipv4_address or node.host
        item["ipv4_probe_status"] = node.ipv4_probe_status
        item["ipv6_probe_status"] = node.ipv6_probe_status
        if node.has_ipv6 and node.ipv6_address:
            item["ipv6_address"] = node.ipv6_address
        else:
            item.pop("ipv6_address", None)
            item.pop("ipv6", None)
        changed = True
    if changed:
        temporary = MH_NODES_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, MH_NODES_FILE.stat().st_mode & 0o777)
        os.replace(temporary, MH_NODES_FILE)


def _adapter(node: Node):
    if node.node_kind in {"proxy", "mtproxy"}:
        return get_proxy_adapter(node)
    return get_adapter_for_node(node)


def _probe_state(probe: Any) -> tuple[str, float | None]:
    if not isinstance(probe, dict) or not isinstance(probe.get("reachable"), bool):
        return "unknown", None
    latency = probe.get("latency_ms")
    return ("online" if probe["reachable"] else "offline"), (float(latency) if latency is not None else None)


def audit_node_network(db: Session, node: Node) -> dict[str, Any]:
    result = _adapter(node).dual_stack_capabilities()
    for field in (
        "has_ipv4",
        "has_ipv6",
        "ipv4_address",
        "ipv6_address",
        "ipv6_subnet_prefix",
        "ipv6_network",
        "ipv6_gateway",
        "network_interface",
        "ipv6_rotation_supported",
    ):
        if field in result:
            setattr(node, field, result[field])
    node.ipv4_probe_status, node.ipv4_latency_ms = _probe_state(result.get("ipv4_probe"))
    node.ipv6_probe_status, node.ipv6_latency_ms = _probe_state(result.get("ipv6_probe"))
    audited_at = result.get("audited_at")
    try:
        node.last_network_audit_at = datetime.fromisoformat(str(audited_at).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        node.last_network_audit_at = datetime.utcnow()
    db.add(node)
    db.commit()
    db.refresh(node)
    _sync_mh_registry(node)
    return result


def probe_relay_link(db: Session, relay: Node) -> None:
    if relay.node_kind != "proxy" or not relay.linked_vpn_node_id:
        return
    exit_node = db.query(Node).filter(Node.id == relay.linked_vpn_node_id).first()
    if not exit_node:
        return
    adapter = get_proxy_adapter(relay)
    target4 = exit_node.ipv4_address or exit_node.host
    result4 = adapter.probe_dual_stack(4, target4)
    relay.ipv4_probe_status, relay.ipv4_latency_ms = _probe_state(result4)
    if exit_node.ipv6_address:
        result6 = adapter.probe_dual_stack(6, exit_node.ipv6_address)
        relay.ipv6_probe_status, relay.ipv6_latency_ms = _probe_state(result6)
    else:
        relay.ipv6_probe_status, relay.ipv6_latency_ms = "unknown", None
    relay.last_network_audit_at = datetime.utcnow()
    db.add(relay)
    db.commit()
    db.refresh(relay)


def set_node_transport_mode(db: Session, node: Node, mode: str) -> Node:
    if mode not in TRANSPORT_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый режим Dual-Stack")
    if mode == "reverse":
        if node.node_kind != "proxy":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reverse Tunnel задаётся на связке proxy-релея с VPN-нодой",
            )
        from app.services.reverse_panel import activate_reverse_tunnel

        activate_reverse_tunnel(db, node)
        return node
    if node.node_kind == "vpn":
        _adapter(node).set_dual_stack_transport(mode)
    elif node.node_kind == "proxy":
        if not node.linked_vpn_node_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proxy-узел не связан с VPN-нодой")
        exit_node = db.query(Node).filter(Node.id == node.linked_vpn_node_id).first()
        if not exit_node:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Связанная VPN-нода не найдена")
        link = db.query(ReverseTunnelLink).filter(ReverseTunnelLink.proxy_node_id == node.id).first()
        try:
            direct_ipv4 = pick_direct_ipv4([exit_node.ipv4_address, node.destination_ip, exit_node.host])
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        # ключ watchdog = id proxy-узла: так же, как link_id в reverse_panel (relay.id)
        get_proxy_adapter(node).set_dual_stack_transport(
            mode,
            link_id=node.id,
            direct_ipv4=direct_ipv4,
            direct_ipv6=exit_node.ipv6_address,
            tunnel_ipv4=link.tunnel_ipv4 if link else None,
        )
        if link:
            link.state = "standby"
            node.reverse_state = "standby"
            db.add(link)
    node.transport_mode = mode
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def rotate_node_ipv6(db: Session, node: Node) -> dict[str, Any]:
    if node.node_kind != "vpn" or not node.ipv6_rotation_supported:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ротация доступна только VPN-узлу с подтверждённой публичной IPv6 /64",
        )
    result = _adapter(node).rotate_ipv6()
    node.ipv6_address = result["ipv6_address"]
    node.ipv6_probe_status = "unknown"
    node.ipv6_latency_ms = None
    node.last_network_audit_at = datetime.utcnow()
    db.add(node)
    db.commit()
    db.refresh(node)
    _sync_mh_registry(node)
    return result


EGRESS_VERIFY_ATTEMPTS = 15
EGRESS_VERIFY_DELAY = 2.0
IPV6_PROBE_TARGET = "2606:4700:4700::1111"


def _verify_egress_applied(adapter: Any, mode: str) -> str:
    """Пусто, если узел после restart реально работает в режиме mode; иначе причина.

    Проверяет состояние, которое пишет up.sh (effective_mode, active_egress), и IPv6-пробу узла.
    Проба идёт с самого узла (node-agent), а не через выходной интерфейс: она ловит полностью мёртвый IPv6, но не доказывает,
    что трафик клиентов идёт именно через Proton/WARP.
    """
    import time

    status_now: dict[str, Any] = {}
    for _ in range(EGRESS_VERIFY_ATTEMPTS):
        try:
            status_now = adapter.get_ipv6_status() or {}
        except Exception as exc:  # noqa: BLE001 - агент мог ещё подниматься после restart
            status_now = {"error": str(exc)}
        if status_now.get("active_egress") == mode and status_now.get("effective_mode") not in (None, "", "a"):
            break
        time.sleep(EGRESS_VERIFY_DELAY)
    else:
        return (
            f"состояние узла: effective_mode={status_now.get('effective_mode')!r}, "
            f"active_egress={status_now.get('active_egress')!r}, причина={status_now.get('reason') or status_now.get('error') or '-'}"
        )
    probe = getattr(adapter, "probe_dual_stack", None)
    if probe:
        try:
            result = probe(6, IPV6_PROBE_TARGET)
        except Exception as exc:  # noqa: BLE001
            return f"IPv6-проба не выполнена: {exc}"
        if isinstance(result, dict) and result.get("reachable") is False:
            return "IPv6-проба узла не прошла"
    return ""


def egress_from_status(status_now: dict[str, Any]) -> str | None:
    """Фактический выход IPv6 из get_ipv6_status(): active_egress, если IPv6 на узле реально включён; иначе None."""
    egress = status_now.get("active_egress")
    if egress in {"native", "proton", "warp"} and status_now.get("effective_mode") not in (None, "", "a"):
        return egress
    return None


def sync_egress_mode_from_status(db: Session, node: Node, status_now: dict[str, Any]) -> bool:
    """Только БД, узел не трогаем: источник истины - состояние узла. Режимы ipv4_only/ipv6_prefer (транспорт) не перезаписываются."""
    actual = egress_from_status(status_now)
    if actual is None or node.egress_mode not in {"native", "proton", "warp"} or node.egress_mode == actual:
        return False
    node.egress_mode = actual
    db.add(node)
    return True


def set_node_egress_mode(db: Session, node: Node, mode: str) -> Node:
    if mode not in EGRESS_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый egress-режим")
    if node.node_kind != "vpn":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Egress настраивается на VPN-узле")
    adapter = _adapter(node)
    if mode in {"native", "proton", "warp"}:
        # селектор карточки = IPV6_EGRESS (выход IPv6 антизапрета); режимы WARP/Proton туннелей (ANTIZAPRET_WARP/VPN_WARP)
        # настраиваются отдельно на вкладке «Конфиг антизапрета» и здесь не меняются
        current = adapter.get_ipv6_status() or {}
        if not current.get("enabled"):  # выход IPv6 имеет смысл только при включённом Dual-Stack сервера; неизвестное = выключено
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Dual-Stack выключен на сервере: сначала включите его, затем выбирайте выход IPv6 (Native/Proton)",
            )
        previous = current.get("egress") or "native"
        adapter.update_antizapret_settings({"IPV6_EGRESS": mode})
        adapter.restart_service("antizapret")
        problem = _verify_egress_applied(adapter, mode)
        if problem:
            # желаемое состояние не выдаём за фактическое: возвращаем прежний выход, БД не трогаем
            adapter.update_antizapret_settings({"IPV6_EGRESS": previous})
            adapter.restart_service("antizapret")
            rollback_problem = _verify_egress_applied(adapter, previous)
            if rollback_problem:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"Выход IPv6 «{mode}» не применился: {problem}. "
                        f"Откат на «{previous}» тоже не подтверждён: {rollback_problem}. Проверьте узел вручную."
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Выход IPv6 «{mode}» не применился: {problem}. Возвращён «{previous}» (подтверждено).",
            )
    elif mode == "ipv4_only":
        adapter.set_dual_stack_transport("force_ipv4")
    else:
        adapter.set_dual_stack_transport("auto")
    node.egress_mode = mode
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def set_node_dual_stack(db: Session, node: Node, enabled: bool) -> dict[str, Any]:
    """Серверный Dual-Stack Вкл/Выкл (ENABLE_IPV6). Профили клиентов (ipv6-profile.txt) и конфиги не трогаются:
    при OFF dual-клиенты работают по IPv4 (awg3_clients.ipv6_enabled), при повторном ON перевыпуск не нужен.
    Узел не подтвердил новое состояние - возвращаем прежнее значение ENABLE_IPV6 и отдаём 502."""
    if node.node_kind != "vpn":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dual-Stack настраивается на VPN-узле")
    adapter = _adapter(node)
    before = adapter.get_ipv6_status() or {}
    was_on = bool(before.get("enabled"))
    if was_on == enabled:
        return {"enabled": enabled, "changed": False}

    def apply(value: bool) -> str:
        updates = {"ENABLE_IPV6": "y" if value else "n"}
        if value:
            updates["IPV6_MODE"] = "c"
            updates["IPV6_EGRESS"] = before.get("egress") or "native"
        adapter.update_antizapret_settings(updates)
        adapter.restart_service("antizapret")
        return _verify_dual_stack_state(adapter, value)

    problem = apply(enabled)
    if problem:
        rollback_problem = apply(was_on)
        tail = "Возвращено прежнее состояние (подтверждено)." if not rollback_problem else f"Откат тоже не подтверждён: {rollback_problem}."
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Dual-Stack не переключён: {problem}. {tail}")
    return {"enabled": enabled, "changed": True}


def _verify_dual_stack_state(adapter: Any, enabled: bool) -> str:
    """Пусто, если узел после restart подтвердил состояние: enabled совпадает, при ON ещё и effective_mode не 'a'."""
    import time

    now: dict[str, Any] = {}
    for _ in range(EGRESS_VERIFY_ATTEMPTS):
        try:
            now = adapter.get_ipv6_status() or {}
        except Exception as exc:  # noqa: BLE001 - агент поднимается после restart
            now = {"error": str(exc)}
        if bool(now.get("enabled")) == enabled and (not enabled or now.get("effective_mode") not in (None, "", "a")):
            return ""
        time.sleep(EGRESS_VERIFY_DELAY)
    return f"enabled={now.get('enabled')!r}, effective_mode={now.get('effective_mode')!r}, причина={now.get('reason') or now.get('error') or '-'}"


def ipv4_egress_from_warp(warp: dict[str, Any]) -> str:
    """Выход IPv4 карточки VPS из ФАКТИЧЕСКИХ настроек WARP узла (up.sh: ANTIZAPRET_OUT_INTERFACE=warp-antizapret только при
    ANTIZAPRET_WARP=2, VPN_WARP=2 включает весь vpn-* через туннель; WARP_PROVIDER выбирает Proton или Cloudflare).
    direct = оба режима 1; proton = провайдер proton и оба режима 2 («весь трафик»); custom = всё остальное (режимы 3/4, смесь,
    Cloudflare): настраивается на вкладке WARP и карточкой не перезаписывается; pending = записано, но не применено up.sh."""
    if warp.get("pending_apply"):
        return "pending"
    az, vpn, provider = str(warp.get("antizapret_warp") or ""), str(warp.get("vpn_warp") or ""), str(warp.get("warp_provider") or "")
    if not az or not vpn:
        return "unknown"
    if az == "1" and vpn == "1":
        return "direct"
    if az == "2" and vpn == "2" and provider == "proton":
        return "proton"
    return "custom"


def set_node_ipv4_egress(db: Session, node: Node, mode: str) -> dict[str, Any]:
    """IPv4: Прямой / Proton поверх существующего механизма WARP (set_warp_provider + set_warp_modes + apply_warp_changes), без второго.

    Применение = up.sh узла: оно на секунды обрывает все туннели, вызывающая сторона обязана подтвердить это в UI.
    Proton без сохранённых ключей up.sh блокирует трафик (WARP_PROTECTION), поэтому без ключей отказ 409 до любой записи.
    При неподтверждённом результате возвращаем прежние провайдер и режимы и снова применяем."""
    if mode not in {"direct", "proton"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IPv4-выход: direct или proton")
    if node.node_kind != "vpn":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="IPv4-выход настраивается на VPN-узле")
    adapter = _adapter(node)
    before = adapter.get_warp_geo_status() or {}
    current = ipv4_egress_from_warp(before)
    if current == mode:
        return {"ipv4_egress": mode, "changed": False}
    if current in {"pending", "unknown"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Состояние WARP узла не подтверждено или не применено: сначала примените его на вкладке WARP")
    if mode == "proton" and not (before.get("proton_antizapret_configured") and before.get("proton_vpn_configured")):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proton не настроен (нужны ключи antizapret и vpn): иначе узел заблокирует трафик")

    def apply(provider: str | None, az: str, vpn: str) -> str:
        if provider:
            adapter.set_warp_provider(provider)
        adapter.set_warp_modes(az, vpn)
        adapter.apply_warp_changes()
        got = ipv4_egress_from_warp(adapter.get_warp_geo_status() or {})
        return "" if got == mode else got

    target_az = target_vpn = "2" if mode == "proton" else "1"
    problem = apply("proton" if mode == "proton" else None, target_az, target_vpn)
    if problem:
        adapter.set_warp_provider(str(before.get("warp_provider") or "cloudflare"))
        adapter.set_warp_modes(str(before.get("antizapret_warp") or "1"), str(before.get("vpn_warp") or "1"))
        adapter.apply_warp_changes()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"IPv4-выход «{mode}» не подтверждён узлом ({problem}); возвращены прежние настройки WARP")
    return {"ipv4_egress": mode, "changed": True}
