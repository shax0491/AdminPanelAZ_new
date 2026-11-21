from types import SimpleNamespace

from app.services.client_local_ip import load_wireguard_local_ip_map

_CONF = "# Client = AT_Cudy_Travell\n[Peer]\nPublicKey = k\nAllowedIPs = {ip}/32\n"


class _Adapter:
    def read_wireguard_server_config(self, interface):
        return _CONF.format(ip="10.29.8.6" if interface == "antizapret" else "10.28.8.6")

    def parse_wireguard_status(self):
        # live dump carries other peers under the same name: must not leak into the card
        return [
            SimpleNamespace(client_name="AT_Cudy_Travell", allowed_ips="10.29.8.4/32"),
            SimpleNamespace(client_name="AT_Cudy_Travell", allowed_ips="10.28.8.10/32"),
            SimpleNamespace(client_name="only_live", allowed_ips="10.29.8.99/32"),
        ]


def test_conf_ips_win_over_live_dump():
    result = load_wireguard_local_ip_map(_Adapter())
    assert result["at_cudy_travell"] == "10.29.8.6, 10.28.8.6"
    assert result["only_live"] == "10.29.8.99"
