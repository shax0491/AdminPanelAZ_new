"""HTTP client for panel ↔ proxy_agent (port 9101 by default).

Clones the RemoteNodeAdapter request/mTLS helpers; does not implement NodeAdapter
(VPN ops). Use ``get_proxy_adapter(node)`` from node_manager.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.dual_stack import endpoint
from app.services.node_mtls import (
    build_node_agent_ssl_context,
    node_agent_base_scheme,
)

HTTP_TIMEOUT = 30.0


class ProxyNodeAdapter:
    """Thin client for proxy_agent health / status / destination / mappings."""

    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        *,
        mtls_enabled: bool = False,
    ):
        self._mtls_enabled = mtls_enabled
        scheme = node_agent_base_scheme(mtls_enabled=mtls_enabled)
        self.base_url = f"{scheme}://{endpoint(host, port)}"
        self.api_key = api_key
        self._verify = build_node_agent_ssl_context(mtls_enabled=mtls_enabled)

    def _get_http_client(self) -> httpx.Client:
        from app.services.node_adapter import shared_http_client

        return shared_http_client(
            self.base_url,
            self._mtls_enabled,
            self._verify,
            timeout=HTTP_TIMEOUT,
        )

    def close(self) -> None:
        """The client is shared per endpoint and closed at process shutdown."""

    def _headers(self) -> dict[str, str]:
        return {"X-Node-Key": self.api_key}

    def _format_connection_error(self, exc: httpx.RequestError) -> str:
        from app.services.node_link_errors import classify_request_error

        _code, message = classify_request_error(exc, mtls_enabled=self._mtls_enabled)
        return message.replace("агенту узла", "proxy_agent").replace("агента узла", "proxy_agent")

    def _request(self, method: str, path: str, **kwargs) -> Any:
        from app.services.node_link_errors import (
            classify_http_status,
            classify_request_error,
            raise_link_error,
        )

        url = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", HTTP_TIMEOUT)
        try:
            client = self._get_http_client()
            response = client.request(
                method,
                url,
                headers=self._headers(),
                timeout=timeout,
                **kwargs,
            )
        except httpx.RequestError as exc:
            code, message = classify_request_error(exc, mtls_enabled=self._mtls_enabled)
            raise_link_error(code, message)

        if response.status_code >= 400:
            detail = response.text
            from_agent = False
            try:
                data = response.json()
                detail = data.get("detail", detail)
                from_agent = True
            except Exception:
                pass
            code, message = classify_http_status(
                response.status_code,
                detail,
                mtls_enabled=self._mtls_enabled,
                from_agent=from_agent,
            )
            raise_link_error(code, message, upstream_status=response.status_code)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()


    def dual_stack_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/dual-stack/capabilities", timeout=30.0)

    def probe_dual_stack(self, family: int, target: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/dual-stack/probe",
            params={"family": family, "target": target},
            timeout=20.0,
        )

    def set_dual_stack_transport(self, mode: str, **payload: Any) -> dict[str, Any]:
        return self._request(
            "POST",
            "/dual-stack/transport",
            json={"mode": mode, **payload},
            timeout=45.0,
        )

    def configure_reverse_relay(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/reverse-tunnel/relay", json=payload, timeout=90.0)

    def teardown_reverse_tunnel(self, link_id: int) -> dict[str, Any]:
        return self._request("DELETE", f"/reverse-tunnel/{link_id}", timeout=30.0)

    def configure_reverse_watchdog(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/reverse-tunnel/watchdog", json=payload, timeout=30.0)

    def reverse_watchdog_status(self, link_id: int) -> dict[str, Any]:
        return self._request("GET", f"/reverse-tunnel/watchdog/{link_id}", timeout=15.0)

    def health(self) -> dict[str, Any]:
        """GET /health → { ok, version }."""
        return self._request("GET", "/health", timeout=10.0)

    def proxy_status(self) -> dict[str, Any]:
        """GET /proxy/status → { installed, destination_ip, detail }."""
        return self._request("GET", "/proxy/status")

    def set_destination(self, ip: str) -> dict[str, Any]:
        """PUT /proxy/destination → updated status."""
        return self._request("PUT", "/proxy/destination", json={"destination_ip": ip})

    def get_mtproxy_status(self) -> dict[str, Any]:
        """GET /mtproxy/status — MTProxyL на узле (тот же формат, что у node_agent)."""
        return self._request("GET", "/mtproxy/status", timeout=40.0)

    def mtproxy_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /mtproxy/action — лимиты/вкл/выкл/ссылка/сброс трафика/перезапуск."""
        return self._request("POST", "/mtproxy/action", json=payload, timeout=150.0)

    def mappings(self) -> dict[str, Any]:
        """GET /proxy/mappings → { mappings: [...] }."""
        return self._request("GET", "/proxy/mappings")

    def failover_status(self, label: str, port: int) -> dict[str, Any]:
        """GET /failover/{label}/status?port=N — независимо от proxy.sh DESTINATION."""
        return self._request("GET", f"/failover/{label}/status", params={"port": port})

    def failover_set_destination(
        self, label: str, port: int, ip: str, *, backend_port: int | None = None
    ) -> dict[str, Any]:
        """PUT /failover/{label}/destination → переключить фронт пула на другой узел.

        ``backend_port`` — реальный порт AmneziaWG на участниках пула, если
        отличается от клиентского ``port`` (общий фронт на несколько пулов)."""
        body: dict[str, Any] = {"destination_ip": ip, "port": port}
        if backend_port is not None:
            body["backend_port"] = backend_port
        return self._request("PUT", f"/failover/{label}/destination", json=body)

    def failover_awg3_status(self, label: str) -> dict[str, Any]:
        """GET /failover-awg3/{label}/status — диапазон клиентских портов AmneziaWG 3."""
        return self._request("GET", f"/failover-awg3/{label}/status")

    def failover_awg3_set_destination(self, label: str, ip: str) -> dict[str, Any]:
        """PUT /failover-awg3/{label}/destination — весь диапазон 51900-51999 на узел ``ip``."""
        return self._request("PUT", f"/failover-awg3/{label}/destination", json={"destination_ip": ip})

    def failover_awg3_teardown(self, label: str) -> dict[str, Any]:
        """DELETE /failover-awg3/{label} — снять правила диапазона AmneziaWG 3."""
        return self._request("DELETE", f"/failover-awg3/{label}")

    def failover_teardown(self, label: str, port: int, *, backend_port: int | None = None) -> dict[str, Any]:
        """DELETE /failover/{label} — снять правила (пул удалён / фронт отвязан)."""
        params: dict[str, Any] = {"port": port}
        if backend_port is not None:
            params["backend_port"] = backend_port
        return self._request("DELETE", f"/failover/{label}", params=params)
