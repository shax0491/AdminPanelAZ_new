"""Состав групп Mihomo, который собирает deploy/mh-app/build_configs.py: Hysteria2 везде, где предусмотрено, Proton нигде в «без Proton»."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).parents[2] / "deploy" / "mh-app"


def _node(key, flag, reserve=False, proton=True):
    return {"flag": flag, "key": key, "host": "203.0.113.%d" % (len(key) + 10), "port_proton": 8445 if proton else None,
            "port_direct": 8443, "uuid": "00000000-0000-0000-0000-000000000000", "sni": "example.com", "pub": "p", "sid": "s",
            "reserve": reserve, "hy2": {"port": 8443, "password": "x", "fingerprint": "ab" * 32, "sni": "bing.com"}}


NODES = [_node("DE2", "🇩🇪"), _node("DE1", "🇩🇪"), _node("DE4", "🇩🇪"), _node("NL2", "🇳🇱"),
         _node("FI1", "🇫🇮", True), _node("FR1", "🇫🇷", True), _node("LV1", "🇱🇻", True)]


@pytest.fixture(scope="module")
def cfg(tmp_path_factory, monkeypatch_module=None):
    base = tmp_path_factory.mktemp("mh")
    for name in ("mihomo.secret", "work.secret", "subscription.url"):
        (base / name).write_text("x")
    (base / "nodes.json").write_text(json.dumps({"nodes": NODES}))
    import os
    os.environ["MH_APP_BASE"] = str(base)
    os.environ["MH_APP_OUT"] = str(base)
    spec = importlib.util.spec_from_file_location("mh_build_configs", DEPLOY / "build_configs.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(DEPLOY))
    spec.loader.exec_module(mod)
    tpl = (DEPLOY / "templates" / "home.yaml").read_text(encoding="utf-8").split("\n")
    global MOD
    MOD = mod
    return yaml.safe_load(mod.build(tpl, NODES, False)), mod.build(tpl, NODES, False)


def _leaf_names():
    out = []
    for n in NODES:
        out += ["%s %s | VLESS Proton" % (n["flag"], n["key"]), "%s %s | VLESS прямой" % (n["flag"], n["key"]),
                "%s %s | Hysteria2" % (n["flag"], n["key"])]
    return out


def _leaves(groups, name, seen=()):
    assert name not in seen, "цикл вложенности: %s" % name
    g = groups[name]
    out = []
    for n in g.get("proxies", []):
        out += _leaves(groups, n, seen + (name,)) if n in groups else [n]
    if "use" in g:
        for n in _leaf_names():
            if re.search(g.get("filter") or "", n):
                out.append(n)
    return out


def test_hysteria2_in_auto_reserve_and_fixed_groups(cfg):
    groups = {g["name"]: g for g in cfg[0]["proxy-groups"]}
    for name in ("Авто — самый быстрый", "Авто — без Proton", "🌍 Резерв: FI1 · FR1 · LV1", "DE прямой (для сайтов с Cloudflare)",
                 "Фикс. выход (стабильный IP)", "🇩🇪 DE1", "🇱🇻 LV1"):
        assert any("Hysteria2" in n for n in _leaves(groups, name)), name
    assert groups["Фикс. выход (стабильный IP)"]["type"] == "fallback"


def test_no_proton_groups_never_reach_proton(cfg):
    groups = {g["name"]: g for g in cfg[0]["proxy-groups"]}
    for name in ("Авто — без Proton", "🌍 Резерв без Proton", "Фикс. выход (стабильный IP)"):
        assert not any("Proton" in n for n in _leaves(groups, name)), name


def test_exceptions_and_ai_direct_groups_are_gone(cfg):
    names = [g["name"] for g in cfg[0]["proxy-groups"]]
    for gone in ("Исключения", "Исключения (без Proton)", "Без Proton: узлы", "ИИ — DE4 прямой"):
        assert gone not in names and ("," + gone) not in cfg[1]
    assert cfg[1].count(",Фикс. выход (стабильный IP)") >= 9  # Filmix и остальные бывшие «Исключения» ведут в Фикс. выход


def test_every_category_has_main_node_hysteria2(cfg):
    groups = {g["name"]: g for g in cfg[0]["proxy-groups"]}
    cats = [n for n, g in groups.items() if g["type"] == "select" and "Авто — самый быстрый" in g.get("proxies", [])]
    assert len(cats) >= 15
    for n in cats:
        hy = [l for l in _leaf_names() if re.search(groups[n].get("filter") or "", l) and "Hysteria2" in l]
        assert {"DE2", "DE1", "DE4", "NL2"} <= {l.split()[1] for l in hy}, n


def test_guard_rejects_category_without_hysteria2(cfg):
    MOD.validate(cfg[1])  # целый конфиг проходит
    broken = yaml.safe_load(cfg[1])
    for g in broken["proxy-groups"]:
        if g["name"] == "Telegram":
            g.pop("use")
            g.pop("filter")
    with pytest.raises(ValueError, match="нет Hysteria2"):
        MOD.validate(yaml.safe_dump(broken, allow_unicode=True))
