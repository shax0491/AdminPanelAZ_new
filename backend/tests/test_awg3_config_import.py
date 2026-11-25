from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import VpnConfig, VpnType
from app.services.config_import import import_clients_from_disk
from tests.test_awg2_config_import import _make_node, _make_owner, db  # noqa: F401


def _adapter(awg3):
    adapter = MagicMock()
    adapter.list_openvpn_clients.return_value = []
    adapter.list_wireguard_clients.return_value = []
    adapter.list_amneziawg2_clients.return_value = []
    if isinstance(awg3, Exception):
        adapter.awg3_list_clients.side_effect = awg3
    else:
        adapter.awg3_list_clients.return_value = awg3
    return adapter


def _count(db, node, name):
    return db.query(VpnConfig).filter_by(node_id=node.id, client_name=name, vpn_type=VpnType.amneziawg3).count()


def test_sync_keeps_awg3_present_on_disk_and_imports_missing(db):
    node, owner = _make_node(db), _make_owner(db)
    db.add(VpnConfig(node_id=node.id, client_name="AB_Phone", vpn_type=VpnType.amneziawg3, owner_id=owner.id))
    db.add(VpnConfig(node_id=node.id, client_name="gone", vpn_type=VpnType.amneziawg3, owner_id=owner.id))
    db.commit()
    disk = [{"name": "AB_Phone_az"}, {"name": "AB_Phone_vpn"}, {"name": "AT_Samsung_8_az"}, {"name": "AT_Samsung_8_vpn"}]
    with patch("app.services.config_import.get_adapter_for_node", return_value=_adapter(disk)):
        result = import_clients_from_disk(db, node, owner.id)
    assert (result.imported, result.removed) == (1, 1)
    assert _count(db, node, "AB_Phone") == 1
    assert _count(db, node, "AT_Samsung_8") == 1
    assert _count(db, node, "gone") == 0


@pytest.mark.parametrize("err", [HTTPException(status_code=502, detail="x"), ConnectionError("x"), OSError("x")])
def test_sync_awg3_probe_failure_keeps_rows(db, err):
    node, owner = _make_node(db), _make_owner(db)
    db.add(VpnConfig(node_id=node.id, client_name="AB_Phone", vpn_type=VpnType.amneziawg3, owner_id=owner.id))
    db.commit()
    with patch("app.services.config_import.get_adapter_for_node", return_value=_adapter(err)):
        result = import_clients_from_disk(db, node, owner.id)
    assert result.removed == 0
    assert _count(db, node, "AB_Phone") == 1


def test_local_ip_map_awg3_merges_az_and_vpn_records():
    from app.services.client_local_ip import build_client_local_ip_map

    adapter = MagicMock()
    adapter.awg3_list_clients.return_value = [
        {"name": "AB_Phone_az", "ip": "10.9.0.2"},
        {"name": "AB_Phone_vpn", "ip": "10.9.1.2"},
        {"name": "other_az", "ip": "10.9.0.9"},
    ]
    cfg = MagicMock(client_name="AB_Phone", vpn_type=VpnType.amneziawg3)
    assert build_client_local_ip_map(adapter, configs=[cfg]) == {"ab_phone": "10.9.0.2, 10.9.1.2"}
