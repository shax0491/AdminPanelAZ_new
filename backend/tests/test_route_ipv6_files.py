from app.constants.public_routes import PUBLIC_ROUTE_ROUTERS
from app.services.cidr.constants import RESULT_FILES


def test_ipv6_route_files_are_registered_and_downloadable():
    pairs = {
        "keenetic-ipv6": ("keenetic_wg6", "keenetic-wireguard-routes6.txt"),
        "keenetic-awg2-ipv6": ("keenetic_awg2_6", "keenetic-amneziawg2-routes6.txt"),
        "keenetic-awg3-ipv6": ("keenetic_awg3_6", "keenetic-amneziawg3-routes6.txt"),
        "mikrotik-ipv6": ("mikrotik_wg6", "mikrotik-wireguard-routes6.txt"),
    }
    for slug, (key, fname) in pairs.items():
        assert PUBLIC_ROUTE_ROUTERS[slug] == key
        assert RESULT_FILES[key] == fname
