"""Lightweight AntiZapret proxy agent — runs on RU proxy hosts (port 9101).

Never installs or runs proxy.sh. DESTINATION edits use iptables only.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from proxy_agent import PROXY_AGENT_VERSION
from proxy_agent.conntrack_maps import parse_conntrack_mappings
from proxy_agent.iptables_dest import (
    IptablesApplyError,
    apply_iptables_plan,
    detect_proxy_destination,
    failover_status_from_rules,
    is_proxy_installed,
    plan_destination_rewrite,
    plan_failover_switch,
    plan_failover_teardown,
    validate_destination_ip,
    validate_failover_label,
    validate_failover_port,
)

PROXY_AGENT_API_KEY = os.environ.get("PROXY_AGENT_API_KEY", "change-me-proxy-agent-key")
PROXY_AGENT_PORT = int(os.environ.get("PROXY_AGENT_PORT", "9101"))
PROXY_AGENT_MODE = os.environ.get("PROXY_AGENT_MODE", "prod").strip().lower()
PROXY_AGENT_ALLOWED_IPS = [
    ip.strip() for ip in os.environ.get("PROXY_AGENT_ALLOWED_IPS", "").split(",") if ip.strip()
]
PROXY_AGENT_MTLS_ENABLED = os.environ.get("PROXY_AGENT_MTLS_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}
PROXY_AGENT_MTLS_SERVER_CERT = os.environ.get(
    "PROXY_AGENT_MTLS_SERVER_CERT", "/etc/adminpanelaz/mtls/agent.crt"
)
PROXY_AGENT_MTLS_SERVER_KEY = os.environ.get(
    "PROXY_AGENT_MTLS_SERVER_KEY", "/etc/adminpanelaz/mtls/agent.key"
)
PROXY_AGENT_MTLS_CA_CERT = os.environ.get(
    "PROXY_AGENT_MTLS_CA_CERT", "/etc/adminpanelaz/mtls/ca.crt"
)
_DEFAULT_PROXY_KEY = "change-me-proxy-agent-key"


def _validate_api_key_or_exit() -> None:
    if PROXY_AGENT_MODE != "prod":
        return
    if not PROXY_AGENT_API_KEY or PROXY_AGENT_API_KEY == _DEFAULT_PROXY_KEY or len(PROXY_AGENT_API_KEY) < 24:
        raise SystemExit(
            "В production задайте PROXY_AGENT_API_KEY (минимум 24 случайных символа). "
            "Пример: openssl rand -hex 32"
        )


_validate_api_key_or_exit()

app = FastAPI(title="AntiZapret Proxy Agent", version=PROXY_AGENT_VERSION)


class ProxyAgentIpAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not PROXY_AGENT_ALLOWED_IPS:
            return await call_next(request)
        client_ip = request.client.host if request.client else ""
        if client_ip.startswith("::ffff:"):
            client_ip = client_ip[7:]
        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён") from None
        for entry in PROXY_AGENT_ALLOWED_IPS:
            try:
                if "/" in entry:
                    if addr in ipaddress.ip_network(entry, strict=False):
                        return await call_next(request)
                elif addr == ipaddress.ip_address(entry):
                    return await call_next(request)
            except ValueError:
                continue
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён с вашего IP")


app.add_middleware(ProxyAgentIpAllowlistMiddleware)


def verify_api_key(x_node_key: str = Header(..., alias="X-Node-Key")) -> None:
    """Auth header name matches node_agent; value checked against PROXY_AGENT_API_KEY."""
    if not x_node_key or not secrets.compare_digest(x_node_key, PROXY_AGENT_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный API-ключ узла")


class DestinationBody(BaseModel):
    destination_ip: str = Field(..., min_length=7, max_length=64)


def _run_iptables_save_nat() -> str:
    try:
        proc = subprocess.run(
            ["iptables-save", "-t", "nat"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"iptables-save недоступен: {exc}",
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"iptables-save ошибка: {err}",
        )
    return proc.stdout or ""


def _apply_iptables_plan(plan: list[list[str]]) -> None:
    try:
        apply_iptables_plan(plan)
    except IptablesApplyError as exc:
        detail = str(exc)
        if exc.rollback_errors:
            detail = f"{detail}; rollback: {'; '.join(exc.rollback_errors)}"
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "недоступен" in detail
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        raise HTTPException(status_code=code, detail=detail) from exc


def _ensure_docker_forward_allows(*ports: int) -> bool:
    """Best-effort self-heal for the Docker-on-a-front-node footgun.

    Docker installs its own DOCKER-USER chain and, via its default
    ``iptables -P FORWARD DROP`` policy, silently swallows any DNAT-relayed
    traffic for a front node that also happens to run Docker (e.g. MTProxy
    on the same box as the AntiZapret front). Confirmed live: DNAT+MASQUERADE
    forwarding works fine until Docker is present, then the forward leg (and,
    separately, the return leg unless ESTABLISHED,RELATED is allowed too)
    just vanishes with no error anywhere. Idempotent — safe to call on every
    switch; no-ops entirely if Docker/DOCKER-USER isn't present on this host.
    Returns True if any rule was actually inserted (caller may want to persist).
    """
    try:
        probe = subprocess.run(
            ["iptables", "-L", "DOCKER-USER", "-n"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if probe.returncode != 0:
        return False  # no DOCKER-USER chain on this host — nothing to heal

    def _ensure_rule(rule: list[str]) -> bool:
        try:
            exists = subprocess.run(
                ["iptables", "-C", "DOCKER-USER", *rule],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if exists.returncode != 0:
                subprocess.run(
                    ["iptables", "-I", "DOCKER-USER", "1", *rule],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return False

    changed = _ensure_rule(["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"])
    for port in {p for p in ports if p}:
        changed = _ensure_rule(["-p", "udp", "--dport", str(port), "-j", "ACCEPT"]) or changed
        changed = _ensure_rule(["-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]) or changed
    return changed


def _persist_iptables() -> None:
    """Persist if netfilter-persistent is available (best-effort)."""
    try:
        subprocess.run(
            ["netfilter-persistent", "save"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _status_from_rules(rules_text: str) -> dict:
    installed = is_proxy_installed(rules_text)
    dest = detect_proxy_destination(rules_text)
    detail = None
    if not installed:
        detail = "Правила AntiZapret proxy (DNAT на известные порты / SNAT) не найдены"
    elif not dest:
        detail = "Прокси-правила есть, но DESTINATION IP не определён"
    return {
        "installed": installed,
        "destination_ip": dest,
        "detail": detail,
    }


@app.get("/health")
def health(_: None = Depends(verify_api_key)):
    started = getattr(app.state, "started_at", None)
    if started is None:
        started = datetime.now(timezone.utc)
        app.state.started_at = started
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "version": PROXY_AGENT_VERSION,
        "agent_version": PROXY_AGENT_VERSION,
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "uptime_sec": max(0, int((now - started).total_seconds())),
        "listen_tls": bool(_uvicorn_ssl_kwargs()),
    }


@app.get("/proxy/status")
def proxy_status(_: None = Depends(verify_api_key)):
    rules = _run_iptables_save_nat()
    return _status_from_rules(rules)


@app.put("/proxy/destination")
def proxy_destination(payload: DestinationBody, _: None = Depends(verify_api_key)):
    try:
        new_ip = validate_destination_ip(payload.destination_ip)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    rules = _run_iptables_save_nat()
    old_ip = detect_proxy_destination(rules)
    if not old_ip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий DESTINATION не найден в nat-правилах (proxy.sh установлен?)",
        )
    if old_ip == new_ip:
        return _status_from_rules(rules)

    try:
        plan = plan_destination_rewrite(rules, old_ip, new_ip)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нет правил для замены DESTINATION",
        )
    _apply_iptables_plan(plan)
    _persist_iptables()
    return _status_from_rules(_run_iptables_save_nat())


class FailoverDestinationBody(BaseModel):
    destination_ip: str = Field(..., min_length=7, max_length=64)
    port: int = Field(..., ge=1, le=65535)
    backend_port: int | None = Field(None, ge=1, le=65535)


@app.get("/failover/{label}/status")
def failover_status(label: str, port: int, _: None = Depends(verify_api_key)):
    try:
        label = validate_failover_label(label)
        port = validate_failover_port(port)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    rules = _run_iptables_save_nat()
    return failover_status_from_rules(rules, label, port)


def _flush_conntrack_for_port(port: int) -> None:
    """Delete existing UDP conntrack entries for this port — without this, a
    client with an already-established session keeps sending to the OLD
    destination for as long as the kernel's conntrack entry survives (Linux
    default: up to ~180s for an [ASSURED] UDP flow), completely ignoring the
    DNAT rule we just changed. Confirmed live: a real AmneziaWG 2.0 client's
    packets kept reaching the old backend after a destination switch until
    this ran, and started reaching the new one within one keepalive interval
    right after. Best-effort — a host without conntrack-tools installed just
    keeps the old ~180s worst case instead of failing the switch outright.
    """
    try:
        subprocess.run(
            ["conntrack", "-D", "-p", "udp", "--dport", str(port)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


@app.put("/failover/{label}/destination")
def failover_set_destination(label: str, payload: FailoverDestinationBody, _: None = Depends(verify_api_key)):
    try:
        label = validate_failover_label(label)
        new_ip = validate_destination_ip(payload.destination_ip)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    docker_healed = _ensure_docker_forward_allows(payload.port, payload.backend_port)

    rules = _run_iptables_save_nat()
    plan = plan_failover_switch(rules, label, payload.port, new_ip, payload.backend_port)
    if plan:
        _apply_iptables_plan(plan)
        _persist_iptables()
        _flush_conntrack_for_port(payload.port)
        if payload.backend_port and payload.backend_port != payload.port:
            _flush_conntrack_for_port(payload.backend_port)
    elif docker_healed:
        _persist_iptables()
    return failover_status_from_rules(_run_iptables_save_nat(), label, payload.port)


@app.delete("/failover/{label}")
def failover_teardown(label: str, port: int, backend_port: int | None = None, _: None = Depends(verify_api_key)):
    try:
        label = validate_failover_label(label)
        port = validate_failover_port(port)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    rules = _run_iptables_save_nat()
    plan = plan_failover_teardown(rules, label, port, backend_port)
    if plan:
        _apply_iptables_plan(plan)
        _persist_iptables()
    return {"label": label, "port": port, "installed": False, "destination_ip": None}


@app.get("/proxy/mappings")
def proxy_mappings(_: None = Depends(verify_api_key)):
    """Best-effort conntrack mappings; empty list if conntrack unavailable."""
    try:
        proc = subprocess.run(
            ["conntrack", "-L"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"mappings": []}
    if proc.returncode != 0:
        return {"mappings": []}
    return {"mappings": parse_conntrack_mappings(proc.stdout or "")}


def _uvicorn_ssl_kwargs() -> dict:
    if not PROXY_AGENT_MTLS_ENABLED:
        return {}
    cert = Path(PROXY_AGENT_MTLS_SERVER_CERT)
    key = Path(PROXY_AGENT_MTLS_SERVER_KEY)
    ca = Path(PROXY_AGENT_MTLS_CA_CERT)
    if not all(p.is_file() for p in (cert, key, ca)):
        return {}
    return {
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
        "ssl_ca_certs": str(ca),
        "ssl_cert_reqs": 2,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "proxy_agent.main:app",
        host="0.0.0.0",
        port=PROXY_AGENT_PORT,
        reload=False,
        **_uvicorn_ssl_kwargs(),
    )
