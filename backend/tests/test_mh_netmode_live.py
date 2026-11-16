"""Живая проверка «Только IPv4» на настоящем Mihomo: AAAA исчезают и возвращаются по dig после DNS flush.

Пропускается, если не задан MH_TEST_MIHOMO (путь к бинарю) или нет dig. Нужен доступ к 1.1.1.1:53 (DNS-апстрим теста).
Запускается на отдельном тестовом Mihomo (порты 28190/28153), боевой Mihomo и его API не затрагиваются.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

BIN = os.environ.get("MH_TEST_MIHOMO", "")
pytestmark = pytest.mark.skipif(not (BIN and os.path.exists(BIN) and shutil.which("dig")), reason="нужны MH_TEST_MIHOMO и dig")

API, SECRET, DNS_PORT = "http://127.0.0.1:28191", "s", "28153"
CARD = "Карточка"
CONFIG = f"""mixed-port: 28190
external-controller: 127.0.0.1:28191
secret: "{SECRET}"
ipv6: true
mode: rule
log-level: warning
profile:
  store-selected: true
dns:
  enable: true
  ipv6: true
  listen: 127.0.0.1:{DNS_PORT}
  enhanced-mode: normal
  nameserver:
    - 1.1.1.1
proxy-groups:
  - name: {CARD}
    type: select
    proxies:
      - DIRECT
      - REJECT
rules:
  - MATCH,{CARD}
"""


def load():
    path = Path(__file__).parents[2] / "deploy" / "mh-app" / "netmode.py"
    spec = importlib.util.spec_from_file_location("mh_netmode_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization": "Bearer " + SECRET})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def dig(name, rtype):
    out = subprocess.run(["dig", "+short", "+time=5", "+tries=1", "-p", DNS_PORT, "@127.0.0.1", name, rtype], capture_output=True, text=True).stdout
    return [x for x in out.split() if x]


@pytest.fixture
def mihomo(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    proc = subprocess.Popen([BIN, "-d", str(tmp_path), "-f", str(tmp_path / "config.yaml")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                api("/version")
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("тестовый Mihomo не поднялся")
        yield tmp_path
    finally:
        proc.terminate()
        proc.wait(10)


def test_ipv4_only_removes_aaaa_keeps_selection_and_rolls_back_bad_payload(mihomo):
    m = load()
    api("/proxies/" + urllib.parse.quote(CARD), "PUT", {"name": "REJECT"})
    assert len(dig("cloudflare.com", "AAAA")) > 0 and len(dig("cloudflare.com", "A")) > 0

    ipv4 = m.set_dns_ipv6(CONFIG, False)
    assert m.apply_config(API, SECRET, ipv4, CONFIG) == "applied"
    assert dig("cloudflare.com", "AAAA") == [] and len(dig("cloudflare.com", "A")) > 0  # AAAA пропали реально, а не только 204
    assert api("/proxies/" + urllib.parse.quote(CARD))["now"] == "REJECT"  # выбор карточки сохранён

    assert m.apply_config(API, SECRET, CONFIG, ipv4) == "applied"
    assert len(dig("cloudflare.com", "AAAA")) > 0  # и вернулись
    assert api("/proxies/" + urllib.parse.quote(CARD))["now"] == "REJECT"

    broken = ipv4.replace("rules:", "rules:\n  - BROKEN,x,y", 1)
    assert m.apply_config(API, SECRET, broken, CONFIG) == "restored"
    assert len(dig("cloudflare.com", "AAAA")) > 0 and len(dig("cloudflare.com", "A")) > 0  # после отката DNS работает как до
