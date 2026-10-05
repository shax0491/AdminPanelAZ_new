"""AmneziaWG 3.0 client lifecycle for interface awg1.

Two modes on the same interface, each with its own subnet and DNS:
- split: antizapret, subnet 10.9.0.0/24, DNS 10.9.0.1, AllowedIPs = list of blocked
  destinations + server subnet (the server only forwards antizapret destinations).
- full: whole-traffic VPN, subnet 10.9.1.0/24, DNS 10.9.1.1, AllowedIPs = 0.0.0.0/0.

Clients live in clients.json next to the server config; the [Peer] blocks are kept in
awg1.conf so the interface keeps them after `awg3@awg1` restarts, and added live with
`awg set`. Client names are unique across modes. Records without "mode" are split.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from app.services.native_awg3_runtime import AWG3_CONF_DIR, AWG3_OBFUSCATION_KEYS

IFACE = "awg1"
PORT = 51821
MTU = 1280
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

MODES: dict[str, dict] = {
    "split": {"subnet": ipaddress.ip_network("10.9.0.0/24"), "server_ip": "10.9.0.1"},
    "full": {"subnet": ipaddress.ip_network("10.9.1.0/24"), "server_ip": "10.9.1.1"},
}
DEFAULT_MODE = "split"

Runner = Callable[[list[str], str | None], str]


class Awg3ClientError(Exception):
    pass


def _default_runner(args: list[str], stdin: str | None) -> str:
    res = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=15, check=False)
    if res.returncode != 0:
        raise Awg3ClientError(res.stderr.strip() or f"command failed: {' '.join(args)}")
    return res.stdout


class Awg3Store:
    """Paths and file I/O for the awg1 server config and client registry."""

    def __init__(self, conf_dir: Path = AWG3_CONF_DIR):
        self.conf_dir = conf_dir
        self.server_conf = conf_dir / f"{IFACE}.conf"
        self.registry = conf_dir / "clients.json"

    def load_clients(self) -> dict:
        if not self.registry.exists():
            return {}
        return json.loads(self.registry.read_text(encoding="utf-8"))

    def save_clients(self, data: dict) -> None:
        tmp = self.registry.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.registry)


def _server_values(server_conf_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in server_conf_text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def _mode_of(record: dict) -> str:
    return record.get("mode", DEFAULT_MODE)


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise Awg3ClientError("name: 1-32 chars, letters, digits, '_' or '-'")


def _next_ip(mode: str, used: set[str]) -> str:
    subnet = MODES[mode]["subnet"]
    server_ip = MODES[mode]["server_ip"]
    for host in subnet.hosts():
        ip = str(host)
        if ip == server_ip or ip in used:
            continue
        return ip
    raise Awg3ClientError(f"subnet {subnet} is full")


def obfuscation_lines(server_conf_text: str) -> list[str]:
    values = _server_values(server_conf_text)
    return [f"{k} = {values[k]}" for k in AWG3_OBFUSCATION_KEYS if k in values]


def allowed_ips_for(mode: str, split_allowed_ips: list[str]) -> list[str]:
    if mode == "full":
        return ["0.0.0.0/0"]
    subnet = str(MODES["split"]["subnet"])
    return [subnet, *[ip for ip in split_allowed_ips if ip != subnet]]


def build_config(
    *,
    mode: str,
    client_private: str,
    client_ip: str,
    server_public: str,
    psk: str,
    endpoint_host: str,
    obfuscation: list[str],
    split_allowed_ips: list[str],
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {client_private}",
        f"Address = {client_ip}/32",
        f"MTU = {MTU}",
        f"DNS = {MODES[mode]['server_ip']}",
        *obfuscation,
        "",
        "[Peer]",
        f"PublicKey = {server_public}",
        f"PresharedKey = {psk}",
        f"Endpoint = {endpoint_host}:{PORT}",
        f"AllowedIPs = {', '.join(allowed_ips_for(mode, split_allowed_ips))}",
        "PersistentKeepalive = 15",
        "",
    ]
    return "\n".join(lines)


def _peer_block(name: str, public: str, psk: str, ip: str) -> str:
    return f"\n[Peer]\n# {name}\nPublicKey = {public}\nPresharedKey = {psk}\nAllowedIPs = {ip}/32\n"


def create_client(
    name: str,
    *,
    mode: str = DEFAULT_MODE,
    endpoint_host: str,
    split_allowed_ips: list[str],
    store: Awg3Store | None = None,
    run: Runner | None = None,
) -> dict:
    if mode not in MODES:
        raise Awg3ClientError(f"unknown mode '{mode}', use split or full")
    _validate_name(name)
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name in clients:
        raise Awg3ClientError(f"client '{name}' already exists")
    if mode == "split" and not split_allowed_ips:
        raise Awg3ClientError("split allowed-ips list is empty")

    server_text = store.server_conf.read_text(encoding="utf-8")
    server_public = run(["awg", "pubkey"], _server_values(server_text)["PrivateKey"]).strip()

    private = run(["awg", "genkey"], None).strip()
    public = run(["awg", "pubkey"], private).strip()
    psk = run(["awg", "genpsk"], None).strip()
    used = {c["ip"] for c in clients.values() if _mode_of(c) == mode}
    ip = _next_ip(mode, used)

    run(["awg", "set", IFACE, "peer", public, "preshared-key", "/dev/stdin", "allowed-ips", f"{ip}/32"], psk)

    with store.server_conf.open("a", encoding="utf-8") as fh:
        fh.write(_peer_block(name, public, psk, ip))

    clients[name] = {"mode": mode, "public_key": public, "ip": ip, "psk": psk, "private_key": private}
    store.save_clients(clients)

    config = build_config(
        mode=mode,
        client_private=private,
        client_ip=ip,
        server_public=server_public,
        psk=psk,
        endpoint_host=endpoint_host,
        obfuscation=obfuscation_lines(server_text),
        split_allowed_ips=split_allowed_ips,
    )
    return {"name": name, "mode": mode, "ip": ip, "public_key": public, "config": config}


def get_client_config(
    name: str,
    *,
    endpoint_host: str,
    split_allowed_ips: list[str],
    store: Awg3Store | None = None,
    run: Runner | None = None,
) -> str:
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name not in clients:
        raise Awg3ClientError(f"client '{name}' not found")
    c = clients[name]
    server_text = store.server_conf.read_text(encoding="utf-8")
    server_public = run(["awg", "pubkey"], _server_values(server_text)["PrivateKey"]).strip()
    return build_config(
        mode=_mode_of(c),
        client_private=c["private_key"],
        client_ip=c["ip"],
        server_public=server_public,
        psk=c["psk"],
        endpoint_host=endpoint_host,
        obfuscation=obfuscation_lines(server_text),
        split_allowed_ips=split_allowed_ips,
    )


def list_clients(store: Awg3Store | None = None) -> list[dict]:
    store = store or Awg3Store()
    return [
        {"name": n, "mode": _mode_of(c), "ip": c["ip"], "public_key": c["public_key"]}
        for n, c in sorted(store.load_clients().items())
    ]


def delete_client(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> None:
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name not in clients:
        raise Awg3ClientError(f"client '{name}' not found")
    public = clients[name]["public_key"]
    run(["awg", "set", IFACE, "peer", public, "remove"], None)

    text = store.server_conf.read_text(encoding="utf-8")
    blocks = text.split("\n[Peer]\n")
    kept = [blocks[0]] + [b for b in blocks[1:] if f"PublicKey = {public}" not in b]
    store.server_conf.write_text("\n[Peer]\n".join(kept), encoding="utf-8")

    del clients[name]
    store.save_clients(clients)


def endpoint_from_env() -> str:
    host = os.environ.get("AWG3_ENDPOINT_HOST", "").strip()
    if not host:
        raise Awg3ClientError("AWG3_ENDPOINT_HOST is not set on this node")
    return host


def split_allowed_from_file(path: str | None = None) -> list[str]:
    file = Path(path or os.environ.get("AWG3_SPLIT_ALLOWED_FILE", "/etc/amnezia/amneziawg3/split-allowed.txt"))
    if not file.is_file():
        raise Awg3ClientError(f"split allowed-ips list not found: {file}")
    items = [line.strip() for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not items:
        raise Awg3ClientError("split allowed-ips list is empty")
    return items
