"""AmneziaWG 3 client lifecycle for interface awg1.

Two modes on the same interface, each with its own subnet and DNS:
- split: antizapret, subnet 10.9.0.0/24, DNS 10.9.0.1, AllowedIPs = list of blocked
  destinations + server subnet (the server only forwards antizapret destinations).
- full: whole-traffic VPN, subnet 10.9.1.0/24, DNS 10.9.1.1, AllowedIPs = 0.0.0.0/0.

Clients live in clients.json next to the server config; the [Peer] blocks are kept in
awg1.conf so the interface keeps them after `awg3@awg1` restarts, and added live with
`awg set`. Client names are unique across modes. Records without "mode" are split.
"""

from __future__ import annotations

import io
import logging
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

from app.services.native_awg3_runtime import AWG3_CONF_DIR, AWG3_OBFUSCATION_KEYS

logger = logging.getLogger(__name__)

IFACE = "awg1"
PORT = 51821  # interface ListenPort; clients may use any port from PORT_RANGE, DNAT'd to PORT on the node
PORT_RANGE = (51900, 51999)
# Client profile files, next to the AmneziaWG 1.5/2.0 ones written by client.sh.
CLIENT_DIR = Path("/root/antizapret/client/amneziawg3")
MTU_DEFAULT = 1280  # used when the node has no mtu file (installs before the auto-MTU step)
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

    def __init__(self, conf_dir: Path = AWG3_CONF_DIR, client_dir: Path = CLIENT_DIR):
        self.conf_dir = conf_dir
        self.client_dir = client_dir
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


def read_mtu(conf_dir: Path = AWG3_CONF_DIR) -> int:
    """MTU written by setup.sh (path MTU minus overhead), clamped to 1280..1420."""
    try:
        value = int((conf_dir / "mtu").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return MTU_DEFAULT
    return min(max(value, MTU_DEFAULT), 1420)


def _mode_of(record: dict) -> str:
    return record.get("mode", DEFAULT_MODE)


# One client = two registry records: "<name>_az" (antizapret, split) and "<name>_vpn" (full VPN).
# The suffix is internal; the panel shows one client named <name>, like AmneziaWG 2.
AZ_SUFFIX = "_az"
VPN_SUFFIX = "_vpn"
BASE_NAME_MAX = 32 - len(VPN_SUFFIX)


def profile_record(name: str, mode: str) -> str:
    return name + (AZ_SUFFIX if mode == "split" else VPN_SUFFIX)


def base_name(record: str) -> str:
    """Client name shown to users: strip the internal suffix from a registry record name."""
    for suffix in (AZ_SUFFIX, VPN_SUFFIX):
        if record.endswith(suffix) and len(record) > len(suffix):
            return record[: -len(suffix)]
    return record


def records_for(name: str, store: "Awg3Store | None" = None) -> list[str]:
    """Registry records that belong to a client: both paired records, or the name itself (legacy)."""
    store = store or Awg3Store()
    clients = store.load_clients()
    paired = [r for r in (profile_record(name, "split"), profile_record(name, "full")) if r in clients]
    return paired or [name]


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise Awg3ClientError("name: 1-32 chars, letters, digits, '_' or '-'")


def _random_port(used: set[int]) -> int:
    free = [p for p in range(PORT_RANGE[0], PORT_RANGE[1] + 1) if p not in used]
    if not free:
        raise Awg3ClientError("no free client ports left in the AWG 3 range")
    return secrets.choice(free)


def _profile_path(store: "Awg3Store", mode: str, name: str) -> Path:
    folder = "antizapret" if mode == "split" else "vpn"
    return store.client_dir / folder / f"{folder}-{name}-awg3.conf"


def _write_profile(store: "Awg3Store", mode: str, name: str, config: str) -> Path:
    path = _profile_path(store, mode, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".conf.tmp")
    tmp.write_text(config, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return path


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
    mtu: int = MTU_DEFAULT,
    port: int = PORT,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {client_private}",
        f"Address = {client_ip}/32",
        f"MTU = {mtu}",
        f"DNS = {MODES[mode]['server_ip']}",
        *obfuscation,
        "",
        "[Peer]",
        f"PublicKey = {server_public}",
        f"PresharedKey = {psk}",
        f"Endpoint = {endpoint_host}:{port}",
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
    port = _random_port({int(c.get("port", PORT)) for c in clients.values()})

    run(["awg", "set", IFACE, "peer", public, "preshared-key", "/dev/stdin", "allowed-ips", f"{ip}/32"], psk)

    with store.server_conf.open("a", encoding="utf-8") as fh:
        fh.write(_peer_block(name, public, psk, ip))

    clients[name] = {"mode": mode, "public_key": public, "ip": ip, "port": port, "psk": psk, "private_key": private}
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
        mtu=read_mtu(store.conf_dir),
        port=port,
    )
    profile = _write_profile(store, mode, name, config)
    return {"name": name, "mode": mode, "ip": ip, "port": port, "public_key": public, "config": config, "profile": str(profile)}


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
    config = build_config(
        mode=_mode_of(c),
        client_private=c["private_key"],
        client_ip=c["ip"],
        server_public=server_public,
        psk=c["psk"],
        endpoint_host=endpoint_host,
        obfuscation=obfuscation_lines(server_text),
        split_allowed_ips=split_allowed_ips,
        mtu=read_mtu(store.conf_dir),
        port=int(c.get("port", PORT)),
    )
    _write_profile(store, _mode_of(c), name, config)
    return config


def list_clients(store: Awg3Store | None = None) -> list[dict]:
    store = store or Awg3Store()
    return [
        {
            "name": n,
            "mode": _mode_of(c),
            "ip": c["ip"],
            "port": int(c.get("port", PORT)),
            "public_key": c["public_key"],
            "suspended": bool(c.get("suspended", False)),
        }
        for n, c in sorted(store.load_clients().items())
    ]


def delete_client(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> None:
    """Delete one registry record, or both paired records when given a client name."""
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name not in clients and any(base_name(r) == name for r in clients):
        for record in records_for(name, store):
            delete_client(record, store=store, run=run)
        return
    if name not in clients:
        raise Awg3ClientError(f"client '{name}' not found")
    public = clients[name]["public_key"]
    run(["awg", "set", IFACE, "peer", public, "remove"], None)

    text = store.server_conf.read_text(encoding="utf-8")
    blocks = text.split("\n[Peer]\n")
    kept = [blocks[0]] + [b for b in blocks[1:] if f"PublicKey = {public}" not in b]
    store.server_conf.write_text("\n[Peer]\n".join(kept), encoding="utf-8")

    _profile_path(store, _mode_of(clients[name]), name).unlink(missing_ok=True)
    del clients[name]
    store.save_clients(clients)


def _drop_peer_block(store: "Awg3Store", public: str) -> None:
    text = store.server_conf.read_text(encoding="utf-8")
    blocks = text.split("\n[Peer]\n")
    kept = [blocks[0]] + [b for b in blocks[1:] if f"PublicKey = {public}" not in b]
    store.server_conf.write_text("\n[Peer]\n".join(kept), encoding="utf-8")


def _apply_to_records(name: str, fn, *, store: Awg3Store | None, run: Runner | None) -> bool:
    changed = False
    for record in records_for(name, store):
        changed = fn(record, store=store, run=run) or changed
    return changed


def suspend_client(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> bool:
    """Suspend the client's records; a bare record name suspends just that record."""
    if name not in (store or Awg3Store()).load_clients():
        return _apply_to_records(name, _suspend_record, store=store, run=run)
    return _suspend_record(name, store=store, run=run)


def _suspend_record(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> bool:
    """Remove the peer from awg1 (live and conf); the registry keeps the keys for unsuspend.

    Returns True when the peer was active and is now removed, False if it was already suspended.
    """
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name not in clients:
        raise Awg3ClientError(f"client '{name}' not found")
    record = clients[name]
    if record.get("suspended"):
        return False
    run(["awg", "set", IFACE, "peer", record["public_key"], "remove"], None)
    _drop_peer_block(store, record["public_key"])
    record["suspended"] = True
    store.save_clients(clients)
    return True


def unsuspend_client(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> bool:
    """Unsuspend the client's records; a bare record name unsuspends just that record."""
    if name not in (store or Awg3Store()).load_clients():
        return _apply_to_records(name, _unsuspend_record, store=store, run=run)
    return _unsuspend_record(name, store=store, run=run)


def _unsuspend_record(name: str, *, store: Awg3Store | None = None, run: Runner | None = None) -> bool:
    """Restore a suspended peer from the node registry (same key, PSK and address)."""
    store = store or Awg3Store()
    run = run or _default_runner
    clients = store.load_clients()
    if name not in clients:
        raise Awg3ClientError(f"client '{name}' not found")
    record = clients[name]
    if not record.get("suspended"):
        return False
    run(
        ["awg", "set", IFACE, "peer", record["public_key"], "preshared-key", "/dev/stdin", "allowed-ips", f"{record['ip']}/32"],
        record["psk"],
    )
    with store.server_conf.open("a", encoding="utf-8") as fh:
        fh.write(_peer_block(name, record["public_key"], record["psk"], record["ip"]))
    record["suspended"] = False
    store.save_clients(clients)
    return True


# Written by setup.sh: the address clients connect to (WIREGUARD_HOST, else the public IP).
SERVER_HOST_FILE = AWG3_CONF_DIR / "server_host"
# Antizapret route list shared with AWG 2, rebuilt by parse.sh: ", ip, ip, ...".
ANTIZAPRET_IPS_FILE = Path("/etc/wireguard/ips")


def get_server_host() -> str | None:
    """Host written into client Endpoint lines (the node's own address, or a failover front)."""
    if SERVER_HOST_FILE.is_file():
        return SERVER_HOST_FILE.read_text(encoding="utf-8").strip() or None
    return None


def set_server_host(host: str) -> str:
    """Point new and refreshed client profiles at ``host`` (used by failover fronts)."""
    host = (host or "").strip()
    if not host or any(ch.isspace() for ch in host) or "/" in host:
        raise Awg3ClientError("server host must be a bare host name or IP")
    SERVER_HOST_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERVER_HOST_FILE.write_text(host + "\n", encoding="utf-8")
    SERVER_HOST_FILE.chmod(0o644)
    return host


def endpoint_from_env() -> str:
    """AWG3_ENDPOINT_HOST, else the server_host file written by setup.sh."""
    host = os.environ.get("AWG3_ENDPOINT_HOST", "").strip()
    if not host and SERVER_HOST_FILE.is_file():
        host = SERVER_HOST_FILE.read_text(encoding="utf-8").strip()
    if not host:
        raise Awg3ClientError("server host unknown: set AWG3_ENDPOINT_HOST on this node")
    return host


def split_allowed_from_file(path: str | None = None) -> list[str]:
    """Antizapret route list: AWG3_SPLIT_ALLOWED_FILE (or path) if set, else the live list /etc/wireguard/ips."""
    explicit = path or os.environ.get("AWG3_SPLIT_ALLOWED_FILE", "").strip()
    file = Path(explicit) if explicit else ANTIZAPRET_IPS_FILE
    if not file.is_file():
        raise Awg3ClientError(f"split allowed-ips list not found: {file}")
    text = file.read_text(encoding="utf-8").replace(",", "\n")
    items = [line.strip() for line in text.splitlines() if line.strip()]
    if not items:
        raise Awg3ClientError("split allowed-ips list is empty")
    return items

# Scripts and unit that run AWG 3 rules (WARP, DNS interception, NAT). The base AntiZapret setup copies them
# once at install time; the panel keeps its own copy in node_agent/awg3_runtime and installs it at agent start,
# so "Update node" alone delivers fixes. Keep these files identical to setup/root/antizapret/awg3 of the base repo.
AWG3_RUNTIME_SRC = Path(__file__).resolve().parents[2] / "node_agent" / "awg3_runtime"
AWG3_RUNTIME_FILES = (
    ("awg3-rules.sh", Path("/usr/local/sbin/awg3-rules.sh"), 0o755),
    ("awg3-up.sh", Path("/usr/local/sbin/awg3-up.sh"), 0o755),
    ("awg3@.service", Path("/etc/systemd/system/awg3@.service"), 0o644),
)


def ensure_awg3_runtime(
    store: "Awg3Store | None" = None,
    *,
    src_dir: Path | None = None,
    files: tuple | None = None,
    run: Callable | None = None,
) -> dict:
    """Install awg3-rules.sh, awg3-up.sh and awg3@.service from the panel copy when they differ. Idempotent.

    Skipped when the AWG 3 layer is not installed (no awg1.conf). daemon-reload runs when the unit changed;
    awg3@awg1 is restarted only when something changed, so repeated agent starts do nothing.
    """
    store = store or Awg3Store()
    if not store.server_conf.is_file():
        return {"skipped": True, "changed": [], "restarted": False, "error": None}
    src_dir = src_dir or AWG3_RUNTIME_SRC
    files = files or AWG3_RUNTIME_FILES
    run = run or subprocess.run
    changed: list[str] = []
    for name, dest, mode in files:
        source = src_dir / name
        if not source.is_file():
            continue
        data = source.read_bytes()
        if dest.is_file() and dest.read_bytes() == data:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".new")
        tmp.write_bytes(data)
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
        changed.append(name)
    if not changed:
        return {"skipped": False, "changed": [], "restarted": False, "error": None}
    logger.info("AWG 3 runtime files updated from the panel copy: %s", ", ".join(changed))
    error = None
    if "awg3@.service" in changed:
        res = run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=60, check=False)
        if res.returncode != 0:
            error = res.stderr.strip() or "systemctl daemon-reload failed"
    if error is None:
        res = run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=60, check=False)
        if res.returncode != 0:
            error = res.stderr.strip() or f"systemctl restart {UNIT} failed"
    if error:
        logger.error("AWG 3 runtime files updated, but applying them failed: %s", error)
    return {"skipped": False, "changed": changed, "restarted": error is None, "error": error}


# Files that make up the AWG 3 layer; the backup archive carries nothing else.
STATE_FILES = ("awg1.conf", "clients.json", "server.key", "server.pub", "split-allowed.txt", "mtu")
STATE_BACKUP_KIND = "awg3-state"
UNIT = f"awg3@{IFACE}"


def export_state_archive(conf_dir: Path = AWG3_CONF_DIR) -> bytes:
    """tar.gz of the AWG 3 layer (server keys, awg1.conf, client registry, split list)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in STATE_FILES:
            path = conf_dir / name
            if path.is_file():
                archive.add(path, arcname=name)
        manifest = STATE_BACKUP_KIND.encode("utf-8")
        info = tarfile.TarInfo("MANIFEST")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    return buffer.getvalue()


def import_state_archive(data: bytes, conf_dir: Path = AWG3_CONF_DIR) -> None:
    """Replace the AWG 3 layer with an archive made by export_state_archive."""
    if not data:
        raise Awg3ClientError("empty AWG 3 backup")
    with tempfile.TemporaryDirectory(prefix="awg3-restore-") as temp_dir:
        temp_root = Path(temp_dir)
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                members = archive.getmembers()
                for member in members:
                    if not member.isfile() or member.name not in (*STATE_FILES, "MANIFEST"):
                        raise Awg3ClientError(f"unexpected member in AWG 3 backup: {member.name}")
                archive.extractall(path=temp_root, members=members, filter="data")
        except tarfile.TarError as exc:
            raise Awg3ClientError(f"invalid AWG 3 backup: {exc}") from exc

        manifest = temp_root / "MANIFEST"
        if not manifest.is_file() or manifest.read_text(encoding="utf-8").strip() != STATE_BACKUP_KIND:
            raise Awg3ClientError(f"AWG 3 backup MANIFEST kind must be {STATE_BACKUP_KIND}")
        if not (temp_root / "awg1.conf").is_file():
            raise Awg3ClientError("AWG 3 backup has no awg1.conf")

        conf_dir.mkdir(parents=True, exist_ok=True)
        for name in STATE_FILES:
            source = temp_root / name
            if not source.is_file():
                continue
            target = conf_dir / name
            tmp = target.with_suffix(target.suffix + ".tmp")
            shutil.copyfile(source, tmp)
            tmp.chmod(0o600)
            tmp.replace(target)


def restart_runtime() -> dict:
    """Restart the awg1 unit after a restore so the restored peers and keys take effect."""
    res = subprocess.run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        return {"success": False, "errors": [res.stderr.strip() or f"systemctl restart {UNIT} failed"]}
    return {"success": True, "errors": []}


# AmneziaWG 3 transport protection. Booleans are written as on/off: the awg tool rejects "true".
TRANSPORT31_LINES = (("ContentPaddingAddition", "2"), ("RandomTrailers", "on"), ("DisableCookies", "on"))


def migrate_transport31(store: "Awg3Store | None" = None) -> bool:
    """Add the 3.1 transport keys to the [Interface] section of awg1.conf. Idempotent.

    Keys go before the first [Peer] block: anything after it would belong to that peer.
    Returns True when the file was changed.
    """
    nl = chr(10)
    peer_marker = nl + "[Peer]" + nl
    store = store or Awg3Store()
    text = store.server_conf.read_text(encoding="utf-8")
    present = _server_values(text)
    missing = [(key, value) for key, value in TRANSPORT31_LINES if key not in present]
    if not missing:
        return False
    add = "".join(key + " = " + value + nl for key, value in missing)
    head, sep, rest = text.partition(peer_marker)
    new_text = head.rstrip(nl) + nl + add + sep + rest
    store.server_conf.write_text(new_text, encoding="utf-8")
    return True


def ensure_transport31(store: "Awg3Store | None" = None) -> dict:
    """Unconditional AmneziaWG 3 migration of this node's awg1.conf; run at agent start.

    Skipped when the AWG 3 layer is not installed on the node (no awg1.conf). Restarts
    awg3@awg1 only when the file actually changed, so repeated starts are harmless.
    """
    store = store or Awg3Store()
    if not store.server_conf.is_file():
        logger.info("AWG 3 transport migration skipped: no awg1.conf on this node")
        return {"skipped": True, "changed": False, "restarted": False, "error": None}
    if not migrate_transport31(store):
        logger.info("AWG 3 transport parameters verified in awg1.conf, no change")
        return {"skipped": False, "changed": False, "restarted": False, "error": None}
    logger.info("AWG 3 transport parameters migrated into awg1.conf, restarting %s", UNIT)
    res = subprocess.run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=60, check=False)
    error = None if res.returncode == 0 else (res.stderr.strip() or f"systemctl restart {UNIT} failed")
    if error is None:
        logger.info("AWG 3 transport parameters migrated successfully, %s restarted", UNIT)
    else:
        logger.error("AWG 3 transport migration done, but %s restart failed: %s", UNIT, error)
    return {"skipped": False, "changed": True, "restarted": error is None, "error": error}
