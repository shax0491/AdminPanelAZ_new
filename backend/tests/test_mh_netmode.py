from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


def load():
    path = Path(__file__).parents[2] / "deploy" / "mh-app" / "netmode.py"
    spec = importlib.util.spec_from_file_location("mh_netmode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG = "ipv6: true\nmixed-port: 1\ndns:\n  enable: true\n  ipv6: true\n  listen: 0.0.0.0:7874\nrules:\n  - MATCH,DIRECT\n"


def test_set_dns_ipv6_changes_only_dns_section_and_is_idempotent():
    m = load()
    off = m.set_dns_ipv6(CONFIG, False)
    assert off.startswith("ipv6: true\n")  # верхнеуровневый ipv6 не трогаем
    assert "dns:\n  enable: true\n  ipv6: false\n" in off
    assert m.set_dns_ipv6(off, False) == off
    assert m.set_dns_ipv6(off, True) == CONFIG  # возврат в «Авто» даёт тот же текст: изменения не накапливаются


def test_set_dns_ipv6_refuses_config_without_dns_ipv6():
    with pytest.raises(ValueError):
        load().set_dns_ipv6("ipv6: true\nrules: []\n", False)


def test_mode_of_card_values():
    m = load()
    assert [m.mode_of(x) for x in ("⚡ Приоритет IPv6", "\U0001f6e1️ Только IPv4", "\U0001f504 Авто (IPv4 + IPv6)", "")] == ["prio", "ipv4", "other", "other"]


class Fake:
    def __init__(self, fail_payloads=()):
        self.calls = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.calls.append((self.command, self.path, json.loads(body)["payload"] if body else None, self.headers.get("Authorization")))
                bad = body and json.loads(body)["payload"] in fail_payloads
                self.send_response(400 if bad else 204)
                self.end_headers()

            do_PUT = do_POST = _do

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def test_apply_config_puts_payload_then_flushes_dns():
    m, fake = load(), Fake()
    try:
        assert m.apply_config(fake.url, "s", "NEW", "OLD") == "applied"
    finally:
        fake.close()
    assert [(c[0], c[1], c[2]) for c in fake.calls] == [("PUT", "/configs?force=true", "NEW"), ("POST", "/cache/dns/flush", None)]
    assert all(c[3] == "Bearer s" for c in fake.calls)
    assert not any("ipv6" in c[1] and "PATCH" in c[0] for c in fake.calls)  # PATCH /configs не используется


def test_apply_config_restores_previous_payload_on_error():
    m, fake = load(), Fake(fail_payloads=("NEW",))
    try:
        assert m.apply_config(fake.url, "s", "NEW", "OLD") == "restored"
    finally:
        fake.close()
    assert [c[2] for c in fake.calls if c[0] == "PUT"] == ["NEW", "OLD"]
    assert fake.calls[-1][1] == "/cache/dns/flush"


def test_apply_config_reports_failed_restore():
    m, fake = load(), Fake(fail_payloads=("NEW", "OLD"))
    try:
        with pytest.raises(RuntimeError, match="откат тоже"):
            m.apply_config(fake.url, "s", "NEW", "OLD")
    finally:
        fake.close()


# ---------- follow_once: одна итерация mode_follow ----------

class Loop:
    """Стенд для follow_once: файлы в tmp, «сборка» пишет в HOME_CONFIG конфиг по текущему ipmode.state, Mihomo - fake HTTP."""

    def __init__(self, tmp_path, fail_payloads=(), home=CONFIG, state="other", build_error=None):
        self.m = load()
        self.state_file = str(tmp_path / "ipmode.state")
        self.home = str(tmp_path / "home-config.yaml")
        if state is not None:
            Path(self.state_file).write_text(state, encoding="utf-8")
        if home is not None:
            Path(self.home).write_text(home, encoding="utf-8")
        self.old_home = home
        self.fake = Fake(fail_payloads)
        self.refreshes = self.builds = 0
        self.logs = []
        self.build_error = build_error
        self.last = None

    def refresh(self):
        self.refreshes += 1

    def rebuild(self):
        self.builds += 1
        if self.build_error:
            raise self.build_error
        ipv4 = Path(self.state_file).read_text(encoding="utf-8").strip() == "ipv4"
        Path(self.home).write_text(self.m.set_dns_ipv6(CONFIG, not ipv4), encoding="utf-8")

    def tick(self, cur):
        self.last = self.m.follow_once(self.last, cur, self.state_file, self.home, self.refresh, self.rebuild, self.fake.url, "s", self.logs.append)

    def puts(self):
        return [c[2] for c in self.fake.calls if c[0] == "PUT"]

    def state(self):
        return Path(self.state_file).read_text(encoding="utf-8").strip()

    def home_text(self):
        return Path(self.home).read_text(encoding="utf-8")

    def close(self):
        self.fake.close()


@pytest.fixture
def loop_factory(tmp_path):
    made = []

    def make(**kw):
        lp = Loop(tmp_path, **kw)
        made.append(lp)
        return lp

    yield make
    for lp in made:
        lp.close()


def test_follow_auto_to_ipv4_and_back_loads_payload_each_way(loop_factory):
    lp = loop_factory()
    lp.tick("other")  # старт: состояние уже «other», ничего не делаем
    assert (lp.refreshes, lp.builds, lp.puts()) == (0, 0, [])
    lp.tick("ipv4")
    assert lp.state() == "ipv4" and "  ipv6: false\n" in lp.home_text()
    assert lp.puts() == [lp.home_text()]
    ipv4_text = lp.home_text()
    lp.tick("other")
    assert lp.state() == "other" and lp.home_text() == CONFIG  # возврат даёт исходный текст
    assert lp.puts() == [ipv4_text, CONFIG]
    assert lp.logs == []
    assert [c[1] for c in lp.fake.calls if c[0] == "POST"] == ["/cache/dns/flush"] * 2  # DNS-кэш сброшен после каждой загрузки


def test_follow_prio_does_not_rebuild_config(loop_factory):
    lp = loop_factory()
    lp.tick("other")
    lp.tick("prio")
    assert (lp.refreshes, lp.builds, lp.puts(), lp.state()) == (1, 0, [], "prio")


def test_follow_build_failure_does_not_loop_and_restores_state(loop_factory):
    lp = loop_factory(build_error=RuntimeError("сборка упала"))
    lp.tick("other")
    for _ in range(5):  # карточка остаётся в «ipv4»: 5 тиков по 2 с
        lp.tick("ipv4")
    assert (lp.refreshes, lp.builds, lp.puts()) == (1, 1, [])
    assert lp.state() == "other" and lp.home_text() == CONFIG
    assert len(lp.logs) == 1 and "НЕ применён" in lp.logs[0] and "сборка упала" in lp.logs[0]


def test_follow_restored_puts_old_config_back_into_home_config(loop_factory):
    lp = loop_factory()
    lp.tick("other")
    lp.fake.close()
    lp.fake = Fake(fail_payloads=(CONFIG.replace("  ipv6: true\n  listen", "  ipv6: false\n  listen"),))
    lp.tick("ipv4")
    assert lp.state() == "other" and lp.home_text() == CONFIG  # роутер по config-sync получит прежний конфиг
    assert len(lp.puts()) == 2 and lp.puts()[1] == CONFIG
    assert len(lp.logs) == 1 and "восстановлены" in lp.logs[0]
    for _ in range(4):
        lp.tick("ipv4")
    assert (lp.builds, len(lp.puts())) == (1, 2)  # цикла нет
    assert not list(Path(lp.home).parent.glob("*.tmp.*"))  # атомарная запись не оставляет временных файлов


def test_follow_new_and_restore_payload_both_fail(loop_factory):
    lp = loop_factory()
    lp.tick("other")
    lp.fake.close()
    new = CONFIG.replace("  ipv6: true\n  listen", "  ipv6: false\n  listen")
    lp.fake = Fake(fail_payloads=(new, CONFIG))
    for _ in range(4):
        lp.tick("ipv4")
    assert lp.state() == "other" and lp.home_text() == CONFIG
    assert (lp.builds, len(lp.puts())) == (1, 2)  # одна попытка и один откат, дальше тишина
    assert len(lp.logs) == 1 and "откат тоже" in lp.logs[0]


@pytest.mark.parametrize("home", [None, "", "  \n"])
def test_follow_without_previous_config_never_loads_empty_payload(loop_factory, home):
    lp = loop_factory(home=home)
    lp.tick("other")
    lp.tick("ipv4")
    assert "" not in lp.puts() and all(p.strip() for p in lp.puts())
    assert lp.puts() == [lp.home_text()] and lp.state() == "ipv4"  # прежнего конфига нет: грузим новый, откат невозможен


def test_follow_without_previous_config_and_failing_load_keeps_state_and_no_loop(loop_factory):
    lp = loop_factory(home=None)
    lp.fake.close()
    lp.fake = Fake(fail_payloads=(CONFIG.replace("  ipv6: true\n  listen", "  ipv6: false\n  listen"),))
    lp.tick("other")
    for _ in range(3):
        lp.tick("ipv4")
    assert lp.state() == "other" and lp.builds == 1 and len(lp.puts()) == 1 and "" not in lp.puts()
    assert len(lp.logs) == 1 and "НЕ применён" in lp.logs[0]


def test_server_py_mode_follow_delegates_to_follow_once():
    """server.py не должен иметь собственной копии логики смены режима: тесты выше проверяют именно тот код, что он вызывает."""
    server = (Path(__file__).parents[2] / "deploy" / "mh-app" / "server.py").read_text(encoding="utf-8")
    body = server[server.index("def mode_follow"):]
    assert "from netmode import follow_once, mode_of" in body
    assert "follow_once(last, cur, IPMODE_FILE, HOME_CONFIG, refresh, rebuild, MIHOMO, SECRET, log)" in body
    assert "apply_config" not in body and "open(IPMODE_FILE" not in body


def test_follow_first_start_with_changed_card_applies_mode(loop_factory):
    lp = loop_factory(state="ipv4", home=CONFIG)  # сервис перезапущен, state=ipv4, карточка уже «Авто»
    lp.tick("other")
    assert lp.state() == "other" and lp.builds == 1 and lp.puts() == [CONFIG]
