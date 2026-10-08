"""Бот: выдача конфигов AmneziaWG 3 (виртуальные файлы awg3:), подписи AWG 2/3, инструкции."""

from __future__ import annotations

from app.models import VpnType
from app.services.profile_delivery import profile_files_for_delivery, read_profile_file_for_delivery
from app.services.telegram_profile_ui import classify_config_profile_groups
from app.services.vpn_install_instructions import build_install_instruction_message


class FakeAdapter:
    def __init__(self):
        self.read = []

    def awg3_list_clients(self):
        return [{"name": "phone_az"}, {"name": "phone_vpn"}, {"name": "other_az"}]

    def awg3_client_config(self, record):
        return f"[Interface]\n# {record}\n"

    def get_profile_files(self, client_name, vpn_type):
        return [{"protocol": "amneziawg2", "path": f"/root/antizapret/client/amneziawg2/antizapret/{client_name}-am2.conf"}]

    def read_profile_file(self, path):
        self.read.append(path)
        return "raw"


def test_awg3_files_are_virtual_records():
    files = profile_files_for_delivery(FakeAdapter(), "phone", VpnType.amneziawg3)
    assert [f["path"] for f in files] == ["awg3:phone_az", "awg3:phone_vpn"]
    assert files[0]["filename"] == "antizapret-phone-awg3.conf"
    assert all(f["protocol"] == "amneziawg3" for f in files)


def test_awg3_content_generated_from_registry():
    adapter = FakeAdapter()
    assert read_profile_file_for_delivery(adapter, "awg3:phone_vpn", []) == "[Interface]\n# phone_vpn\n"
    assert adapter.read == []


def test_other_types_use_node_files():
    files = profile_files_for_delivery(FakeAdapter(), "phone", VpnType.amneziawg2)
    assert files[0]["protocol"] == "amneziawg2"


def test_awg2_and_awg3_labelled_as_amneziawg():
    assert classify_config_profile_groups([{"protocol": "amneziawg3"}], VpnType.amneziawg3) == frozenset({"awg"})
    assert classify_config_profile_groups([{"protocol": "amneziawg2"}], VpnType.amneziawg2) == frozenset({"awg"})


def test_awg3_install_instruction_names_supported_apps():
    text = build_install_instruction_message(protocol="amneziawg3", platform="android", client_name="phone")
    assert "v3.1.20260814" in text and "AmneziaVPN" in text
    assert "AmneziaWG 3.1" in build_install_instruction_message(protocol="awg3", platform="ios", client_name="x")
