import csv
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status

from app.config import get_settings
from app.models import VpnType
from app.schemas import MonitoringService, OpenVpnClient, WireGuardPeer
from app.services.antizapret_backup import AntizapretBackupService
from app.services.openvpn_management import openvpn_management_service
from app.services.profile_files import iter_client_profile_paths, profile_filename_matches_client

settings = get_settings()
CLIENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
WIREGUARD_SERVER_INTERFACES = frozenset({"antizapret", "vpn"})
WIREGUARD_SERVER_CONFIG_DIR = Path("/etc/wireguard")
WIREGUARD_CLIENT_PROFILE_DIRS = ("wireguard", "amneziawg")
OPENVPN_CLIENT_PROFILE_DIR = "openvpn"
EASYRSA3_ROOT = Path("/etc/openvpn/easyrsa3")
EASYRSA_INDEX_PATH = EASYRSA3_ROOT / "pki" / "index.txt"

# Native AmneziaWG 2.0 (compiled by setup.sh from amneziawg-go/amneziawg-tools, files *-am2.conf).
# Deliberately separate from the third-party az-awg2 overlay (app.services.awg2 / /etc/amnezia/amneziawg) —
# that overlay is no longer wired into the panel and its binaries are not expected to be installed.
NATIVE_AWG2_SERVER_DIR = Path("/etc/amneziawg")
NATIVE_AWG2_TUNNELS: dict[str, tuple[Path, str]] = {
    "antizapret": (NATIVE_AWG2_SERVER_DIR / "antizapret2.conf", "antizapret"),
    "vpn": (NATIVE_AWG2_SERVER_DIR / "vpn2.conf", "vpn"),
}
NATIVE_AWG2_OBFUSCATION_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4")
NATIVE_AWG2_CLIENT_MTU = 1280
NATIVE_AWG2_SERVER_INTERFACES = frozenset({"antizapret2", "vpn2"})


def _parse_client_names_section(output: str, header: str) -> list[str]:
    """Extract client names listed under `header` in unified `client.sh 3` output.

    client.sh's option 3 prints three sections back to back (OpenVPN, then
    WireGuard/AmneziaWG 1.5, then AmneziaWG 2.0), each starting with its own header line and
    ending at the next blank line — so section-aware parsing is required instead of a flat
    line filter.
    """
    names: list[str] = []
    capturing = False
    for line in output.splitlines():
        stripped = line.strip()
        if not capturing:
            if stripped.startswith(header):
                capturing = True
            continue
        if not stripped:
            break
        names.append(stripped)
    return names


class AntiZapretService:
    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or settings.antizapret_path
        self.client_script = self.base_path / "client.sh"
        self.client_dir = self.base_path / "client"
        self.config_dir = self.base_path / "config"
        self.openvpn_logs = Path("/etc/openvpn/server/logs")

    def _run_client_script(self, *args: str, timeout: int = 120) -> str:
        if not self.client_script.exists():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Скрипт AntiZapret не найден: {self.client_script}",
            )
        cmd = [str(self.client_script), *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Таймаут выполнения команды AntiZapret",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ошибка запуска AntiZapret: {exc}",
            ) from exc

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка AntiZapret: {output.strip() or 'неизвестная ошибка'}",
            )
        return output.strip()

    def validate_client_name(self, name: str) -> str:
        if not CLIENT_NAME_RE.match(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Имя клиента: 1–32 символа (a-z, A-Z, 0-9, _, -)",
            )
        return name

    def add_openvpn_client(self, client_name: str, cert_expire_days: int = 3650) -> str:
        self.validate_client_name(client_name)
        return self._run_client_script("1", client_name, str(cert_expire_days))

    def delete_openvpn_client(self, client_name: str) -> str:
        self.validate_client_name(client_name)
        return self._run_client_script("2", client_name)

    def list_openvpn_clients(self) -> list[str]:
        output = self._run_client_script("3")
        return _parse_client_names_section(output, "OpenVPN client names:")

    def add_wireguard_client(self, client_name: str) -> str:
        # client.sh no longer has a protocol-specific "add wireguard" option: option 1 is a
        # single unified add that creates OpenVPN + WireGuard + AmneziaWG 1.5 + AmneziaWG 2.0
        # profiles together (idempotent — reuses existing keys/peers for a name that already
        # has some of those profiles). See _run_client_script docstring notes below.
        self.validate_client_name(client_name)
        return self._run_client_script("1", client_name)

    def delete_wireguard_client(self, client_name: str) -> str:
        self.validate_client_name(client_name)
        return self._run_client_script("2", client_name)

    def list_wireguard_clients(self) -> list[str]:
        output = self._run_client_script("3")
        return _parse_client_names_section(output, "WireGuard/AmneziaWG 1.5 client names:")

    def add_amneziawg2_client(self, client_name: str) -> str:
        """Add/refresh a client's native AmneziaWG 2.0 (*-am2.conf) profiles via client.sh.

        client.sh generates keys with the native `awg` binary and appends a [Peer] block via
        `awg syncconf` (diff-only peer sync — never rewrites the interface's Address/PostUp or
        restarts the tunnel). After generation we rewrite the resulting client file(s) so their
        obfuscation params always match the live server interface and MTU is forced to 1280.
        """
        self.validate_client_name(client_name)
        output = self._run_client_script("1", client_name)
        self._apply_native_awg2_overrides(client_name)
        return output

    def delete_amneziawg2_client(self, client_name: str) -> str:
        self.validate_client_name(client_name)
        return self._run_client_script("2", client_name)

    def list_amneziawg2_clients(self) -> list[str]:
        output = self._run_client_script("3")
        return _parse_client_names_section(output, "AmneziaWG 2.0 client names:")

    def get_amneziawg2_monitoring(self) -> dict[str, object]:
        from app.services.native_awg2_runtime import get_monitoring

        return get_monitoring()

    def get_amneziawg2_client_stats(self, client_name: str) -> dict[str, object] | None:
        from app.services.native_awg2_runtime import get_client_stats

        return get_client_stats(client_name)

    def get_amneziawg2_health(self) -> dict[str, object]:
        """Native readiness check — the `awg` binary is what client.sh's AmneziaWG 2.0
        functions shell out to (`awg genkey`/`awg pubkey`/`awg genpsk`/`awg syncconf`)."""
        awg_bin = shutil.which("awg")
        missing: list[str] = []
        if not awg_bin:
            missing.append("awg_binary")
        return {
            "installed": bool(awg_bin),
            "awg_binary": bool(awg_bin),
            "server_dir": NATIVE_AWG2_SERVER_DIR.is_dir(),
            "missing_components": missing,
        }

    def _read_native_awg2_server_obfuscation(self, server_conf: Path) -> dict[str, str]:
        """Read Jc/Jmin/Jmax/S1-4/H1-4 as actually configured on the live [Interface] block."""
        if not server_conf.is_file():
            return {}
        return self._read_native_awg2_server_obfuscation_from_text(
            server_conf.read_text(encoding="utf-8", errors="replace")
        )

    def _read_native_awg2_server_obfuscation_from_text(self, text: str) -> dict[str, str]:
        params: dict[str, str] = {}
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped.startswith("[Peer]"):
                break
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key in NATIVE_AWG2_OBFUSCATION_KEYS:
                params[key] = value.strip()
        return params

    def _apply_native_awg2_overrides(self, client_name: str) -> None:
        for _tunnel, (server_conf, subdir) in NATIVE_AWG2_TUNNELS.items():
            obfuscation = self._read_native_awg2_server_obfuscation(server_conf)
            directory = self.client_dir / "amneziawg2" / subdir
            if not directory.is_dir():
                continue
            for profile_path in directory.glob(f"*-{client_name}-am2.conf"):
                self._rewrite_native_awg2_client_file(profile_path, obfuscation)

    @staticmethod
    def _conf_line_key(line: str) -> str:
        stripped = line.strip()
        return stripped.split("=", 1)[0].strip() if "=" in stripped else ""

    def _rewrite_native_awg2_client_file(self, path: Path, obfuscation: dict[str, str]) -> None:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        # Decide the MTU strategy up front: if an MTU line already exists anywhere (regardless
        # of position), only rewrite it in place — otherwise insert a fresh one right after
        # Address. Deciding this per-line instead would insert-then-also-rewrite and duplicate
        # the line whenever MTU isn't immediately adjacent to Address.
        has_mtu_line = any(self._conf_line_key(line) == "MTU" for line in lines)

        output_lines: list[str] = []
        mtu_written = False
        for line in lines:
            key = self._conf_line_key(line)
            if key == "MTU":
                output_lines.append(f"MTU = {NATIVE_AWG2_CLIENT_MTU}")
                mtu_written = True
                continue
            if key in obfuscation:
                output_lines.append(f"{key} = {obfuscation[key]}")
                continue
            output_lines.append(line)
            if key == "Address" and not has_mtu_line and not mtu_written:
                output_lines.append(f"MTU = {NATIVE_AWG2_CLIENT_MTU}")
                mtu_written = True
        if not mtu_written:
            output_lines.insert(1, f"MTU = {NATIVE_AWG2_CLIENT_MTU}")
        path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    def recreate_profiles(self) -> str:
        return self._run_client_script("4", timeout=300)

    def read_easyrsa_index(self) -> str:
        if not EASYRSA_INDEX_PATH.is_file():
            return ""
        return EASYRSA_INDEX_PATH.read_text(encoding="utf-8", errors="replace")

    def get_openvpn_cert_expiry_map(self) -> dict[str, str]:
        """CN → ISO-8601 UTC notAfter for every still-valid OpenVPN client certificate."""
        from app.services.openvpn_pki import cert_expiry_map_by_cn, parse_easyrsa_index

        result: dict[str, str] = {}
        for cn, not_after in cert_expiry_map_by_cn(parse_easyrsa_index(self.read_easyrsa_index())).items():
            result[cn] = not_after.strftime("%Y-%m-%dT%H:%M:%SZ")
        return result

    def _wireguard_server_config_path(self, interface: str) -> Path:
        normalized = (interface or "").strip().lower()
        if normalized not in WIREGUARD_SERVER_INTERFACES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый WireGuard interface: {interface}",
            )
        return WIREGUARD_SERVER_CONFIG_DIR / f"{normalized}.conf"

    def read_wireguard_server_config(self, interface: str) -> str:
        path = self._wireguard_server_config_path(interface)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_wireguard_server_config(self, interface: str, content: str) -> None:
        path = self._wireguard_server_config_path(interface)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")

    def apply_wireguard_runtime(self) -> dict:
        from app.services.wg_runtime import sync_all_wireguard_interfaces

        return sync_all_wireguard_interfaces()

    def export_easyrsa3_archive(self) -> bytes:
        if not EASYRSA3_ROOT.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Каталог easyrsa3 не найден: {EASYRSA3_ROOT}",
            )
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for item in sorted(EASYRSA3_ROOT.rglob("*")):
                if not item.is_file():
                    continue
                arcname = item.relative_to(EASYRSA3_ROOT.parent).as_posix()
                archive.add(item, arcname=arcname)
        return buffer.getvalue()

    def import_easyrsa3_archive(self, data: bytes) -> None:
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пустой архив easyrsa3",
            )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(data)
                temp_path = tmp.name
            if EASYRSA3_ROOT.is_dir():
                shutil.rmtree(EASYRSA3_ROOT, ignore_errors=True)
            with tarfile.open(temp_path, "r:gz") as archive:
                for member in archive.getmembers():
                    if member.name == "easyrsa3" or member.name.startswith("easyrsa3/"):
                        archive.extract(member, path="/etc/openvpn", filter="data")
            if not EASYRSA3_ROOT.is_dir():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Архив easyrsa3 не содержит каталог easyrsa3",
                )
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def create_antizapret_backup(self) -> dict[str, str]:
        return AntizapretBackupService(install_dir=self.base_path).create_backup()

    def restore_antizapret_backup(self, archive_path: str) -> dict[str, str]:
        return AntizapretBackupService(install_dir=self.base_path).restore_backup(archive_path)

    def restore_antizapret_backup_for_ha_replica(self, archive_path: str) -> dict[str, str]:
        return AntizapretBackupService(install_dir=self.base_path).restore_backup_for_ha_replica(archive_path)

    def list_wireguard_server_config_files(self) -> list[str]:
        if not WIREGUARD_SERVER_CONFIG_DIR.is_dir():
            return []
        return sorted(path.name for path in WIREGUARD_SERVER_CONFIG_DIR.glob("*.conf") if path.is_file())

    def delete_wireguard_server_config_file(self, filename: str) -> None:
        if not filename.endswith(".conf") or "/" in filename or ".." in filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимое имя WireGuard config: {filename}",
            )
        path = WIREGUARD_SERVER_CONFIG_DIR / filename
        if path.is_file():
            path.unlink()

    def _amneziawg2_server_config_path(self, interface: str) -> Path:
        normalized = (interface or "").strip().lower()
        if normalized not in NATIVE_AWG2_SERVER_INTERFACES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый AmneziaWG 2.0 interface: {interface}",
            )
        return NATIVE_AWG2_SERVER_DIR / f"{normalized}.conf"

    def list_amneziawg2_server_config_files(self) -> list[str]:
        if not NATIVE_AWG2_SERVER_DIR.is_dir():
            return []
        return sorted(
            path.name
            for path in NATIVE_AWG2_SERVER_DIR.glob("*.conf")
            if path.is_file() and path.stem in NATIVE_AWG2_SERVER_INTERFACES
        )

    def read_amneziawg2_server_config(self, interface: str) -> str:
        path = self._amneziawg2_server_config_path(interface)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_amneziawg2_server_config(self, interface: str, content: str) -> None:
        path = self._amneziawg2_server_config_path(interface)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")

    def delete_amneziawg2_server_config_file(self, filename: str) -> None:
        if not filename.endswith(".conf") or "/" in filename or ".." in filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимое имя AmneziaWG 2.0 config: {filename}",
            )
        path = NATIVE_AWG2_SERVER_DIR / filename
        if path.is_file():
            path.unlink()

    def read_amneziawg2_server_key(self) -> str:
        """The `PRIVATE_KEY=/PUBLIC_KEY=` file client.sh sources when rendering new client
        profiles (`source "$AWG2/key"`) — must match the identity baked into the copied
        server .conf files, or profiles rendered later on a promoted replica get the wrong
        server PublicKey."""
        key_path = NATIVE_AWG2_SERVER_DIR / "key"
        if not key_path.is_file():
            return ""
        return key_path.read_text(encoding="utf-8", errors="replace")

    def write_amneziawg2_server_key(self, content: str) -> None:
        if not content:
            return
        key_path = NATIVE_AWG2_SERVER_DIR / "key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(content, encoding="utf-8")

    def apply_amneziawg2_runtime(self) -> dict:
        from app.services.native_awg2_runtime import sync_all_native_awg2_interfaces

        return sync_all_native_awg2_interfaces()

    def export_amneziawg2_client_profiles_archive(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            root = self.client_dir / "amneziawg2"
            if root.is_dir():
                for item in sorted(root.rglob("*")):
                    if not item.is_file():
                        continue
                    arcname = "client/" + item.relative_to(self.client_dir).as_posix()
                    archive.add(item, arcname=arcname)
        return buffer.getvalue()

    def import_amneziawg2_client_profiles_archive(self, data: bytes) -> None:
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пустой архив профилей AmneziaWG 2.0",
            )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(data)
                temp_path = tmp.name
            with tarfile.open(temp_path, "r:gz") as archive:
                has_profile_file = any(
                    member.isfile() and member.name.startswith("client/amneziawg2/")
                    for member in archive.getmembers()
                )
                if not has_profile_file:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Архив профилей AmneziaWG 2.0 не содержит файлов client/amneziawg2",
                    )
            root = self.client_dir / "amneziawg2"
            if root.is_dir():
                shutil.rmtree(root)
            with tarfile.open(temp_path, "r:gz") as archive:
                for member in archive.getmembers():
                    name = member.name
                    if not (name.startswith("client/amneziawg2/") or name == "client/amneziawg2"):
                        continue
                    archive.extract(member, path=str(self.base_path), filter="data")
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def get_antizapret_fingerprints(self) -> dict[str, str]:
        return AntizapretBackupService(install_dir=self.base_path).get_fingerprints()

    def get_config_file_fingerprints(self) -> dict[str, str]:
        return AntizapretBackupService(install_dir=self.base_path).get_config_file_fingerprints()

    def get_profile_files(self, client_name: str, vpn_type: VpnType) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        if vpn_type == VpnType.openvpn:
            search_dirs = [
                ("openvpn", "antizapret", "antizapret", ".ovpn"),
                ("openvpn", "antizapret-udp", "antizapret-udp", "-udp.ovpn"),
                ("openvpn", "antizapret-tcp", "antizapret-tcp", "-tcp.ovpn"),
                ("openvpn", "vpn", "vpn", ".ovpn"),
                ("openvpn", "vpn-udp", "vpn-udp", "-udp.ovpn"),
                ("openvpn", "vpn-tcp", "vpn-tcp", "-tcp.ovpn"),
            ]
            prefix = "antizapret" if client_name.startswith("antizapret") else "vpn"
            for proto, variant, label, suffix in search_dirs:
                directory = self.client_dir / proto / variant
                if not directory.exists():
                    continue
                for path in directory.glob(f"*{client_name}*{suffix}"):
                    if not profile_filename_matches_client(path.name, client_name, suffix=suffix):
                        continue
                    files.append({
                        "protocol": "openvpn",
                        "variant": label,
                        "filename": path.name,
                        "path": str(path),
                    })
                for path in directory.glob(f"{prefix}-*{suffix}"):
                    if profile_filename_matches_client(path.name, client_name, suffix=suffix) and not any(
                        f["path"] == str(path) for f in files
                    ):
                        files.append({
                            "protocol": "openvpn",
                            "variant": label,
                            "filename": path.name,
                            "path": str(path),
                        })
        elif vpn_type == VpnType.amneziawg2:
            for variant, suffix in [
                ("antizapret", "-am2.conf"),
                ("vpn", "-am2.conf"),
            ]:
                directory = self.client_dir / "amneziawg2" / variant
                for path in iter_client_profile_paths(directory, client_name, suffix):
                    files.append({
                        "protocol": "amneziawg2",
                        "variant": variant,
                        "filename": path.name,
                        "path": str(path),
                    })
        else:
            for proto, variant, suffix in [
                ("wireguard", "antizapret", "-wg.conf"),
                ("wireguard", "vpn", "-wg.conf"),
                ("amneziawg", "antizapret", "-am.conf"),
                ("amneziawg", "vpn", "-am.conf"),
            ]:
                directory = self.client_dir / proto / variant
                for path in iter_client_profile_paths(directory, client_name, suffix):
                    files.append({
                        "protocol": proto,
                        "variant": variant,
                        "filename": path.name,
                        "path": str(path),
                    })
        return files

    def read_profile_file(self, path: str) -> str:
        file_path = self._resolve_profile_file_path(path)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
        return file_path.read_text(encoding="utf-8", errors="replace")

    def write_profile_file(self, path: str, content: str) -> None:
        file_path = self._resolve_profile_file_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content or "", encoding="utf-8")

    def _resolve_profile_file_path(self, path: str) -> Path:
        file_path = Path(path).resolve()
        client_root = self.client_dir.resolve()
        if not str(file_path).startswith(str(client_root)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ к файлу запрещён")
        return file_path

    def export_wireguard_client_profiles_archive(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for subdir in WIREGUARD_CLIENT_PROFILE_DIRS:
                root = self.client_dir / subdir
                if not root.is_dir():
                    continue
                for item in sorted(root.rglob("*")):
                    if not item.is_file():
                        continue
                    arcname = ("client/" + item.relative_to(self.client_dir).as_posix())
                    archive.add(item, arcname=arcname)
        return buffer.getvalue()

    def import_wireguard_client_profiles_archive(self, data: bytes) -> None:
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пустой архив профилей WireGuard",
            )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(data)
                temp_path = tmp.name
            with tarfile.open(temp_path, "r:gz") as archive:
                has_profile_file = any(
                    member.isfile()
                    and (
                        member.name.startswith("client/wireguard/")
                        or member.name.startswith("client/amneziawg/")
                    )
                    for member in archive.getmembers()
                )
                if not has_profile_file:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Архив профилей WireGuard не содержит файлов client/wireguard или client/amneziawg",
                    )
            for subdir in WIREGUARD_CLIENT_PROFILE_DIRS:
                root = self.client_dir / subdir
                if root.is_dir():
                    shutil.rmtree(root)
            with tarfile.open(temp_path, "r:gz") as archive:
                for member in archive.getmembers():
                    name = member.name
                    if not (
                        name.startswith("client/wireguard/")
                        or name.startswith("client/amneziawg/")
                        or name in {"client/wireguard", "client/amneziawg"}
                    ):
                        continue
                    archive.extract(member, path=str(self.base_path), filter="data")
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def export_openvpn_client_profiles_archive(self) -> bytes:
        buffer = io.BytesIO()
        root = self.client_dir / OPENVPN_CLIENT_PROFILE_DIR
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            if root.is_dir():
                for item in sorted(root.rglob("*")):
                    if not item.is_file():
                        continue
                    arcname = "client/" + item.relative_to(self.client_dir).as_posix()
                    archive.add(item, arcname=arcname)
        return buffer.getvalue()

    def import_openvpn_client_profiles_archive(self, data: bytes) -> None:
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пустой архив профилей OpenVPN",
            )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(data)
                temp_path = tmp.name
            with tarfile.open(temp_path, "r:gz") as archive:
                has_profile_file = any(
                    member.isfile() and member.name.startswith("client/openvpn/")
                    for member in archive.getmembers()
                )
                if not has_profile_file:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Архив профилей OpenVPN не содержит файлов client/openvpn",
                    )
            openvpn_root = self.client_dir / OPENVPN_CLIENT_PROFILE_DIR
            if openvpn_root.is_dir():
                shutil.rmtree(openvpn_root)
            with tarfile.open(temp_path, "r:gz") as archive:
                for member in archive.getmembers():
                    name = member.name
                    if not (
                        name.startswith("client/openvpn/")
                        or name == "client/openvpn"
                    ):
                        continue
                    archive.extract(member, path=str(self.base_path), filter="data")
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    _CONFIG_FILES = frozenset(
        {
            "include-hosts.txt",
            "exclude-hosts.txt",
            "include-ips.txt",
            "exclude-ips.txt",
            "allow-ips.txt",
            "drop-ips.txt",
            "forward-ips.txt",
            "include-adblock-hosts.txt",
            "exclude-adblock-hosts.txt",
            "remove-hosts.txt",
            "deny-ips.txt",
            "banned_clients",
        }
    )

    def read_config_file(self, filename: str) -> str:
        allowed = self._CONFIG_FILES
        if filename not in allowed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый конфигурационный файл")
        path = self.config_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_config_file(self, filename: str, content: str) -> None:
        allowed = self._CONFIG_FILES
        if filename not in allowed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл недоступен для записи")
        path = self.config_dir / filename
        path.write_text(content, encoding="utf-8")

    def restart_service(self, service_name: str) -> str:
        allowed = {
            "openvpn-server@antizapret-udp",
            "openvpn-server@antizapret-tcp",
            "openvpn-server@vpn-udp",
            "openvpn-server@vpn-tcp",
            "wg-quick@antizapret",
            "wg-quick@vpn",
        }
        if service_name not in allowed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимое имя службы")
        try:
            result = subprocess.run(
                ["systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Таймаут перезапуска службы") from exc
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=output.strip() or "Ошибка перезапуска")
        return output.strip() or "ok"

    def reboot(self) -> str:
        try:
            result = subprocess.run(
                ["systemctl", "reboot", "--no-wall"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Таймаут команды reboot",
            ) from exc
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=output or "Ошибка reboot",
            )
        return output or "reboot issued"

    def list_openvpn_server_confs(self) -> list[str]:
        from app.services.openvpn_multihome import OPENVPN_SERVER_CONF_NAMES

        server_dir = Path("/etc/openvpn/server")
        if not server_dir.is_dir():
            return []
        return [name for name in OPENVPN_SERVER_CONF_NAMES if (server_dir / name).is_file()]

    def _openvpn_server_conf_path(self, filename: str) -> Path:
        from app.services.openvpn_multihome import OPENVPN_SERVER_CONF_NAMES

        name = (filename or "").strip()
        if name not in OPENVPN_SERVER_CONF_NAMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый OpenVPN server conf: {filename}",
            )
        return Path("/etc/openvpn/server") / name

    def read_openvpn_server_conf(self, filename: str) -> str:
        path = self._openvpn_server_conf_path(filename)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_openvpn_server_conf(self, filename: str, content: str) -> None:
        path = self._openvpn_server_conf_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")

    def get_openvpn_multihome_status(self) -> dict:
        from app.services.openvpn_multihome import conf_has_bare_multihome

        confs = self.list_openvpn_server_confs()
        present: list[str] = []
        missing: list[str] = []
        for name in confs:
            content = self.read_openvpn_server_conf(name)
            if conf_has_bare_multihome(content):
                present.append(name)
            else:
                missing.append(name)
        on_disk = bool(confs) and not missing
        return {
            "on_disk": on_disk,
            "confs": confs,
            "present": present,
            "missing": missing,
        }

    def ensure_openvpn_multihome(self, enabled: bool) -> dict:
        """Patch OpenVPN server confs, then restart setup-enabled active units."""
        from app.services.antizapret_settings import read_protocol_enable_flags
        from app.services.node_sync.openvpn_restart import restart_all_openvpn_servers
        from app.services.openvpn_multihome import apply_multihome_to_conf

        enabled = bool(enabled)
        confs = self.list_openvpn_server_confs()
        patched: list[str] = []
        unchanged: list[str] = []
        for name in confs:
            raw = self.read_openvpn_server_conf(name)
            new = apply_multihome_to_conf(raw, enabled)
            if new != raw:
                self.write_openvpn_server_conf(name, new)
                patched.append(name)
            else:
                unchanged.append(name)

        protocol_flags = read_protocol_enable_flags(self.base_path / "setup")

        # LocalAdapter duck-types restart_service / get_service_status /
        # get_antizapret_settings for restart_all_openvpn_servers.
        class _RestartProxy:
            def __init__(self, service: "AntiZapretService"):
                self._service = service

            def get_service_status(self):
                return self._service.get_service_status()

            def restart_service(self, service_name: str) -> str:
                return self._service.restart_service(service_name)

            def get_antizapret_settings(self) -> dict[str, str]:
                return dict(protocol_flags)

        restart_result = restart_all_openvpn_servers(
            _RestartProxy(self),
            protocol_flags=protocol_flags,
        )
        status = self.get_openvpn_multihome_status()
        return {
            "success": bool(restart_result.get("success", True)),
            "enabled": enabled,
            "patched": patched,
            "unchanged": unchanged,
            "restart": restart_result,
            "on_disk": status.get("on_disk"),
            "confs": status.get("confs") or confs,
            "present": status.get("present") or [],
            "missing": status.get("missing") or [],
        }

    def apply_config_changes(self) -> str:
        doall = self.base_path / "doall.sh"
        if not doall.exists():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="doall.sh не найден")
        try:
            result = subprocess.run(
                [str(doall)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                cwd=str(self.base_path),
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Таймаут обновления списков") from exc
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=output.strip())
        return output.strip()

    def get_server_ip(self) -> str | None:
        try:
            result = subprocess.run(
                ["bash", "-c", "ip route get 1.2.3.4 2>/dev/null | grep -oP 'src \\K\\S+'"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ip = result.stdout.strip()
            return ip or None
        except (subprocess.TimeoutExpired, OSError):
            return None

    def get_antizapret_version(self) -> str | None:
        version_file = self.base_path / "VERSION"
        if version_file.is_file():
            version = version_file.read_text(encoding="utf-8", errors="replace").strip()
            if version:
                return version
        try:
            result = subprocess.run(
                ["git", "-C", str(self.base_path), "describe", "--tags", "--always"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                if version:
                    return version
        except (subprocess.TimeoutExpired, OSError):
            pass
        return None

    def get_service_status(self) -> list[MonitoringService]:
        from app.services.antizapret_settings import (
            is_vpn_monitor_service_expected,
            read_protocol_enable_flags,
        )

        services = [
            "openvpn-server@antizapret-udp",
            "openvpn-server@antizapret-tcp",
            "openvpn-server@vpn-udp",
            "openvpn-server@vpn-tcp",
            "wg-quick@antizapret",
            "wg-quick@vpn",
        ]
        # Skip units disabled in setup (e.g. OPENVPN_TCP_ENABLE=n) so NOC
        # incidents / health score do not treat intentional stop as failure.
        enable_flags = read_protocol_enable_flags(self.base_path / "setup")
        expected = [svc for svc in services if is_vpn_monitor_service_expected(svc, enable_flags)]
        if not expected:
            return []

        states: list[str]
        try:
            result = subprocess.run(
                ["systemctl", "is-active", *expected],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            states = [line.strip() or "unknown" for line in result.stdout.splitlines()]
        except (subprocess.TimeoutExpired, OSError):
            states = ["unknown"] * len(expected)

        # Pad / trim so a partial systemctl response cannot desync names vs states.
        if len(states) < len(expected):
            states.extend(["unknown"] * (len(expected) - len(states)))
        elif len(states) > len(expected):
            states = states[: len(expected)]

        return [
            MonitoringService(
                name=svc,
                status=state,
                active=state == "active",
                description="Служба VPN" if "openvpn" in svc or "wg" in svc else None,
            )
            for svc, state in zip(expected, states)
        ]

    def parse_openvpn_status(self) -> tuple[list[OpenVpnClient], str]:
        clients, data_source = openvpn_management_service.collect_clients(self.openvpn_logs)
        return clients, data_source

    def parse_openvpn_status_legacy(self) -> list[OpenVpnClient]:
        """Parse OpenVPN clients from *-status.log files (legacy fallback)."""
        clients: list[OpenVpnClient] = []
        if not self.openvpn_logs.exists():
            return clients
        for log_file in sorted(self.openvpn_logs.glob("*-status.log")):
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            reader = csv.reader(io.StringIO(content))
            in_client_list = False
            for row in reader:
                if not row:
                    continue
                if row[0] == "HEADER" and len(row) > 1 and row[1] == "CLIENT_LIST":
                    in_client_list = True
                    continue
                if row[0] == "HEADER":
                    in_client_list = False
                    continue
                if row[0] == "END":
                    in_client_list = False
                    continue
                if in_client_list and row[0] == "CLIENT_LIST" and len(row) >= 9:
                    try:
                        clients.append(
                            OpenVpnClient(
                                common_name=row[1],
                                real_address=row[2],
                                virtual_address=row[3],
                                bytes_received=int(row[6] or 0),
                                bytes_sent=int(row[7] or 0),
                                connected_since=row[8],
                            )
                        )
                    except (ValueError, IndexError):
                        continue
        return clients

    def parse_wireguard_status(self) -> list[WireGuardPeer]:
        peers: list[WireGuardPeer] = []
        try:
            result = subprocess.run(["wg", "show", "all", "dump"], capture_output=True, text=True, timeout=10, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return peers
        if result.returncode != 0:
            return peers

        wg_clients = self._load_wg_client_map()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            interface, public_key, _psk, endpoint, allowed_ips, latest_handshake, rx, tx = parts[:8]
            client_name = wg_clients.get(public_key)
            handshake = None
            if latest_handshake and latest_handshake != "0":
                try:
                    handshake = datetime.utcfromtimestamp(int(latest_handshake)).isoformat()
                except ValueError:
                    handshake = latest_handshake
            peers.append(
                WireGuardPeer(
                    interface=interface,
                    public_key=public_key,
                    endpoint=endpoint or None,
                    allowed_ips=allowed_ips or None,
                    latest_handshake=handshake,
                    transfer_rx=int(rx or 0),
                    transfer_tx=int(tx or 0),
                    client_name=client_name,
                )
            )
        return peers

    def _load_wg_client_map(self) -> dict[str, str]:
        conf_paths = [Path("/etc/wireguard/antizapret.conf"), Path("/etc/wireguard/vpn.conf")]
        signature = tuple(
            (str(conf), conf.stat().st_mtime_ns if conf.exists() else None)
            for conf in conf_paths
        )
        cached = getattr(self, "_wg_client_map_cache", None)
        if cached and cached.get("signature") == signature:
            return dict(cached.get("mapping") or {})

        mapping: dict[str, str] = {}
        for conf in conf_paths:
            if not conf.exists():
                continue
            current_client: str | None = None
            for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# Client ="):
                    current_client = line.split("=", 1)[1].strip()
                elif line.strip().startswith("PublicKey =") and current_client:
                    pub = line.split("=", 1)[1].strip()
                    mapping[pub] = current_client
        self._wg_client_map_cache = {"signature": signature, "mapping": mapping}
        return mapping


antizapret_service = AntiZapretService()
