"""notify.py: читаемые русские уведомления, дедуп, защита от дребезга, восстановление не теряется."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    path = Path(__file__).parents[2] / "deploy" / "mh-app" / "notify.py"
    spec = importlib.util.spec_from_file_location("mh_notify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


N = load()
HY = "🇩🇪 DE4 | Hysteria2"


def test_hy2_down_needs_two_snapshots_and_recovery_is_immediate():
    w = N.Watcher()
    assert w.hy2({HY: False}) == []
    down = w.hy2({HY: False})
    assert len(down) == 1 and "недоступен" in down[0] and "DE4" in down[0] and "Traceback" not in down[0]
    assert w.hy2({HY: False}) == []                     # без спама
    up = w.hy2({HY: True})
    assert len(up) == 1 and "восстановлен" in up[0]
    assert w.hy2({HY: True}) == []


def test_single_blip_is_silent():
    w = N.Watcher()
    assert w.hy2({HY: False}) == [] and w.hy2({HY: True}) == []


def test_transport_switch_cooldown_but_reserve_return_always_sent():
    t = [1000.0]
    w = N.Watcher(clock=lambda: t[0])
    g = "Авто — самый быстрый"
    assert w.auto(g, "🇩🇪 DE4 | VLESS Proton") == []
    assert "Hysteria2" in w.auto(g, HY)[0]
    t[0] += 30
    assert w.auto(g, "🇩🇪 DE4 | VLESS Proton") == []   # дребезг внутри COOLDOWN
    t[0] += 700
    assert "🛟" in w.auto(g, "🇫🇮 FI1 | VLESS прямой")[0]
    t[0] += 5
    back = w.auto(g, "🇩🇪 DE4 | VLESS прямой")
    assert back and "↩️" in back[0]                        # возврат не теряется


def test_same_transport_other_node_is_not_an_event():
    w = N.Watcher()
    w.auto("g", "🇩🇪 DE4 | VLESS прямой")
    assert w.auto("g", "🇩🇪 DE2 | VLESS прямой") == []


def test_link_loss_is_not_server_failure_and_recovers():
    w = N.Watcher()
    assert w.link(False) == [] and w.link(False) == []
    lost = w.link(False)
    assert len(lost) == 1 and "не значит, что серверы упали" in lost[0]
    assert w.link(False) == []
    assert "восстановлена" in w.link(True)[0]


def test_mode_change_and_apply_error_have_no_technical_noise():
    w = N.Watcher()
    assert w.mode("🔄 Авто (IPv4 + IPv6)") == []
    assert "Только IPv4" in w.mode("🛡️ Только IPv4")[0]
    msg = N.apply_error("сетевой режим", "прежний режим сохранён", "ipv4", "Command '['sudo']' returned non-zero exit status 1.")
    assert "Traceback" not in msg and "returned non-zero" not in msg and "пересобрать" in msg


def test_country_flags_are_kept_in_messages():
    w = N.Watcher()
    w.hy2({HY: False})
    assert "🇩🇪 DE4" in w.hy2({HY: False})[0]
    assert "🇩🇪 DE4" in w.hy2({HY: True})[0]
    w.auto("g", "🇩🇪 DE4 | VLESS прямой")
    msg = w.auto("g", "🇫🇮 FI1 | VLESS прямой")[0]
    assert "🇩🇪 DE4, VLESS прямой" in msg and "🇫🇮 FI1, VLESS прямой" in msg
