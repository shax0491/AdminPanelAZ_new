#!/usr/bin/env python3
"""mh-app: Telegram Mini App для управления mihomo на домашнем роутере.

Без внешних зависимостей. Слушает порт из config.json, наружу выставляется через NPM.
Вход только по подписи Telegram initData + список разрешённых user id.
Из mihomo наружу открыто ровно: чтение состояния, выбор узла в группе-селекторе,
замер задержек. Перезапуск, правка конфига, соединения, логи - недоступны.
"""
import hashlib, hmac, json, os, re, sys, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
TOKEN = CFG["bot_token"]
ALLOWED = set(int(x) for x in CFG["allowed_ids"])
MIHOMO = CFG["mihomo_url"].rstrip("/")
SECRET = open(os.path.join(BASE, "mihomo.secret"), encoding="utf-8").read().strip()
AUTO = CFG.get("auto_group", "Авто — самый быстрый")
AUTO2 = CFG.get("auto2_group", "Авто — AWG 2.0")
AUTO3 = CFG.get("auto3_group", "Авто — AWG 3.1")
CHECK = CFG.get("check_group", "Все серверы — проверка")
PORT = int(CFG.get("port", 8790))
HOME_MIHOMO = bool(CFG.get("home_mihomo", True))
HIST_FILE = os.path.join(BASE, "history.json")
HIDE_GROUPS = set(CFG.get("hide_groups", ["GLOBAL"]))
MAX_AGE = 86400

_lock = threading.Lock()
_last_delay_test = 0.0
_rate = {}



# ---------- домашний роутер: запрет (nfqws) и AWG через ограниченный SSH-ключ (на роутере разрешены только rtctl-команды) ----------
import subprocess
RT_HOST = CFG.get("router_host", "192.168.1.1")
RT_PORT = str(CFG.get("router_port", 2222))
RT_KEY = os.path.join(BASE, "ssh", "router_key")
RT_KNOWN = os.path.join(BASE, "ssh", "known_hosts")
FAIL_FILE = os.path.join(BASE, "auto_failover.json")
DEFAULT_ORDER = ["DE2", "DE1", "NL2"]  # по скорости нативного AWG (замеры 13.10)
_rt_cache = {"t": 0, "v": None}
_rt_lock = threading.Lock()
AWG_NAMES = ("none", "DE1", "DE2", "NL2")


def rt_run(cmd, timeout=25):
    r = subprocess.run(["ssh", "-p", RT_PORT, "-i", RT_KEY, "-o", "UserKnownHostsFile=" + RT_KNOWN, "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "root@" + RT_HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "ssh ошибка").strip().splitlines()[-1][:120])
    return r.stdout


_lists_cache = {"t": 0, "v": None}


def az_lists():
    """Списки AntiZapret из панели (собирает /opt/az-lists/build.sh, на оба роутера приходят провайдерами az-*)."""
    def _az(*names):
        out = []
        for n in names:
            try:
                out += [x.strip().lstrip("+.") for x in open("/var/www/az-lists/" + n, encoding="utf-8") if x.strip() and not x.startswith("#")]
            except OSError:
                pass
        return out
    return {"vpn": _az("az-include.txt", "az-include-ips.txt"), "direct": _az("az-exclude.txt", "az-exclude-ips.txt")}


def rt_lists():
    """Пользовательские списки mihomo на роутере: domain/IP, которые идут в VPN, напрямую или блокируются."""
    if _lists_cache["v"] and time.time() - _lists_cache["t"] < 5:
        return _lists_cache["v"]
    v = {}
    for k in ("vpn", "direct", "block", "noproton"):
        v[k] = [x for x in rt_run("list items " + k).splitlines() if x.strip() and not x.startswith("список:")]
    v["az"] = az_lists()
    _lists_cache["t"], _lists_cache["v"] = time.time(), v
    return v


def failover_cfg():
    try:
        return json.load(open(FAIL_FILE, encoding="utf-8"))
    except Exception:
        return {"on": False, "order": DEFAULT_ORDER}


def rt_state(force=False):
    with _rt_lock:
        if not force and _rt_cache["v"] and time.time() - _rt_cache["t"] < 6:
            return _rt_cache["v"]
        d = {}
        for ln in rt_run("status").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
        code = int(d.get("yt_code", "0") or 0)
        v = {"zapret": d.get("zapret") == "on", "pkts": d.get("zapret_pkts", ""), "yt_ip": d.get("yt_ip", ""), "yt_via": d.get("yt_via", ""),
             "yt_code": code, "yt_ok": 200 <= code < 400, "yt_time": d.get("yt_time", ""), "awg": d.get("awg", "none"),
             "awg_ping": d.get("awg_ping", ""), "awgs": [x for x in d.get("awg_list", "").split(",") if x], "failover": failover_cfg(), "strategy": int(d.get("zapret_strategy", "1") or 1),
             "scores": [dict(zip(("n", "ok", "ms"), (int(a), int(b), int(c)))) for a, b, c in
                        (x.split(":") for x in d.get("zapret_scores", "").split() if x.count(":") == 2)], "t": int(time.time())}
        _rt_cache["t"], _rt_cache["v"] = time.time(), v
        return v


# ---------- подписка для mihomo (proxy-provider): список VLESS-Reality узлов по доменам, автообновляется из реестра nodes.json ----------
NODES_FILE = os.path.join(BASE, "nodes.json")
SUB_TOKEN_FILE = os.path.join(BASE, "sub.token")


def build_sub():
    from subscription import build_subscription

    nodes = json.load(open(NODES_FILE, encoding="utf-8"))["nodes"]
    return build_subscription(nodes)


def mihomo(path, method="GET", body=None, timeout=15):
    req = urllib.request.Request(MIHOMO + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + SECRET, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def tg_manual(text):
    """Сообщение в бот о ручном переключении из Mini App (в отдельном потоке, ответ приложению не ждёт)."""
    if os.path.exists("/run/mh-app.mute"):  # идёт автотест (/root/miniapp_test.py) - никаких сообщений
        return

    def run():
        try:  # группа уведомлений (как у сторожа роутера); не вышло - в личку, как раньше
            g = json.load(open("/opt/arr-stack/scripts/tg_group.json"))
            data = urllib.parse.urlencode({"chat_id": g["chat"], "message_thread_id": g.get("thread") or "", "text": text,
                                           "disable_web_page_preview": "true"}).encode()
            if json.loads(urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % g["token"], data=data, timeout=15).read()).get("ok"):
                return
        except Exception:
            pass
        for uid in ALLOWED:
            try:
                data = urllib.parse.urlencode({"chat_id": uid, "text": text, "disable_web_page_preview": "true"}).encode()
                urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % TOKEN, data=data, timeout=15).read()
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()


def verify_init_data(init_data):
    """Возвращает user dict, если подпись верна, данные свежие и id разрешён."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        if not got:
            return None
        dcs = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
        key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(key, dcs.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > MAX_AGE:
            return None
        user = json.loads(pairs.get("user", "{}"))
        if int(user.get("id", 0)) not in ALLOWED:
            return None
        return user
    except Exception:
        return None


def load_hist():
    try:
        return json.load(open(HIST_FILE, encoding="utf-8"))
    except Exception:
        return []


def save_hist(h):
    tmp = HIST_FILE + ".tmp"
    json.dump(h[-100:], open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, HIST_FILE)


def poller():
    """Раз в 30 с запоминает смену выбранного сервера в группе «Авто»."""
    last = None
    tick = 0
    while True:
        tick += 1
        if tick % 30 == 1:
            try:
                q = urllib.parse.urlencode({"url": "http://www.gstatic.com/generate_204", "timeout": 5000})
                mihomo("/group/%s/delay?%s" % (urllib.parse.quote(CHECK), q), timeout=25)
            except Exception:
                pass
        try:
            now = mihomo("/proxies/" + urllib.parse.quote(AUTO)).get("now")
            if now and last and now != last:
                with _lock:
                    h = load_hist()
                    h.append({"t": int(time.time()), "group": AUTO, "from": last, "to": now})
                    save_hist(h)
            if now:
                last = now
        except Exception:
            pass
        time.sleep(30)


def mode_plan(px, mode):
    """Что переключить, чтобы все карточки пошли через AWG 2.0 (mode=awg2) или обратно на AWG 3.1 (mode=awg3)."""
    plan = []
    for name, g in px.items():
        if g.get("type") != "Selector" or name in HIDE_GROUPS or name == CHECK:
            continue
        now = g.get("now") or ""
        allo = g.get("all", [])
        if mode == "awg2":
            tgt = AUTO2 if now in (AUTO, AUTO3) else (now.replace(" | AWG3", " | AWG2") if " | AWG3" in now else None)
        else:
            tgt = (AUTO3 if AUTO3 in allo else AUTO) if now in (AUTO, AUTO2) else (now.replace(" | AWG2", " | AWG3") if " | AWG2" in now else None)
        if tgt and tgt != now and tgt in allo:
            plan.append((name, now, tgt))
    return plan


def build_state_from(px, ver, hist):
    """Состояние для страницы из сырого ответа mihomo /proxies (общая логика для «дом» и «работа»)."""
    def own_delay(name):
        h = (px.get(name) or {}).get("history") or []
        return h[-1]["delay"] if h and h[-1].get("delay") else None

    def leaf(name):
        seen = set()
        while name in px and px[name].get("type") in ("Selector", "URLTest", "Fallback") and name not in seen:
            seen.add(name)
            nxt = px[name].get("now")
            if not nxt:
                break
            name = nxt
        return name, own_delay(name)

    def shown(name):
        # вложенная группа автовыбора («Резерв»): её собственный замер - по нему решает «Авто»,
        # а не задержка выбранного внутри неё сервера (разные замеры, расходятся на десятки мс)
        if (px.get(name) or {}).get("type") in ("URLTest", "Selector", "Fallback") and own_delay(name):
            return own_delay(name)
        return leaf(name)[1]

    auto = px.get(AUTO, {})
    auto_nodes = [{"name": n, "delay": shown(n)} for n in auto.get("all", [])]
    chk = px.get(CHECK, {})
    extra = []
    for n in chk.get("all", []):
        if n not in auto.get("all", []):
            extra.append({"name": n, "delay": leaf(n)[1], "leaf": px.get(n, {}).get("dialer-proxy") or None})
    groups = []
    for name, p in px.items():
        if p.get("type") not in ("Selector", "URLTest", "Fallback") or name in HIDE_GROUPS or name == AUTO or p.get("hidden"):
            continue
        opts = []
        for n in p.get("all", []):
            tp = (px.get(n) or {}).get("type", "")
            lf, dl = leaf(n)[0], shown(n)
            opts.append({"name": n, "delay": dl, "group": tp in ("URLTest", "Fallback", "Selector"), "leaf": lf if lf != n else None})
        lf, dl = leaf(p.get("now") or "")
        groups.append({"name": name, "type": p.get("type"), "fixed": p.get("fixed") or None, "now": p.get("now"), "icon": p.get("icon"), "now_leaf": lf, "now_delay": dl, "options": opts})
    # порядок карточек как в панели mihomo: список GLOBAL.all идёт в порядке конфига; запасной вариант - group_order из config.json
    glob = (px.get("GLOBAL") or {}).get("all") or CFG.get("group_order", [])
    order = {n: i for i, n in enumerate(glob)}
    groups.sort(key=lambda g: (order.get(g["name"], 999), g["name"]))
    n2 = sum(1 for g in groups if g["name"] != CHECK and ((g["now"] or "") == AUTO2 or " | AWG2" in (g["now"] or "")))
    n3 = sum(1 for g in groups if g["name"] != CHECK and ((g["now"] or "") in (AUTO, AUTO3) or " | AWG3" in (g["now"] or "")))
    mode = {"available": AUTO2 in px, "awg2": n2, "awg3": n3,
            "value": "mixed" if (n2 and n3) else ("awg2" if n2 else "awg3")}
    return {"version": ver, "now": int(time.time()), "mode": mode,
            "auto": {"name": AUTO, "now": auto.get("now"), "fixed": auto.get("fixed") or None, "icon": auto.get("icon"), "nodes": auto_nodes},
            "extra": extra, "groups": groups, "history": hist[-30:][::-1]}


def urltest_order(px):
    """Незакреплённые группы url-test: сначала те, что входят в другие (резерв), затем остальные."""
    ut = [n for n, p in px.items() if p.get("type") == "URLTest" and not p.get("fixed")]
    inner = [n for n in ut if any(n in (px[o].get("all") or []) for o in ut if o != n)]
    return inner + [n for n in ut if n not in inner]


def merge_providers(px):
    """Узлы из proxy-provider (подписка VLESS) не попадают в /proxies - берём их историю задержек из /providers/proxies."""
    try:
        for pv in mihomo("/providers/proxies", timeout=10).get("providers", {}).values():
            if pv.get("vehicleType") == "Compatible":
                continue
            for p in pv.get("proxies", []):
                px.setdefault(p.get("name"), p)
    except Exception:
        pass
    return px


def build_state():
    px = merge_providers(mihomo("/proxies").get("proxies", {}))
    ver = ""
    try:
        ver = mihomo("/version", timeout=5).get("version")
    except Exception:
        pass
    with _lock:
        hist = load_hist()
    return build_state_from(px, ver, hist)


# ---------- рабочий роутер: он сам присылает состояние и забирает команды ----------
AGENT_KEY_FILE = os.path.join(BASE, "agent.key")
AGENT_KEY = open(AGENT_KEY_FILE, encoding="utf-8").read().strip() if os.path.exists(AGENT_KEY_FILE) else ""
WORK_HIST_FILE = os.path.join(BASE, "history_work.json")
WORK = {"px": None, "ver": "", "info": {}, "t": 0}
WORK_CMDS = []


def work_hist():
    try:
        return json.load(open(WORK_HIST_FILE, encoding="utf-8"))
    except Exception:
        return []


def work_report(data):
    px = data.get("px")
    if not isinstance(px, dict) or not px:
        raise ValueError("пустое состояние")
    with _lock:
        prev = (WORK["px"] or {}).get(AUTO, {}).get("now")
        cur = px.get(AUTO, {}).get("now")
        if prev and cur and prev != cur:
            h = work_hist()
            h.append({"t": int(time.time()), "group": AUTO, "from": prev, "to": cur})
            tmp = WORK_HIST_FILE + ".tmp"
            json.dump(h[-100:], open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, WORK_HIST_FILE)
        WORK.update({"px": px, "ver": str(data.get("ver") or ""), "info": data.get("info") or {}, "t": time.time()})
        cmds = list(WORK_CMDS)
        WORK_CMDS.clear()
    return cmds


def work_state():
    with _lock:
        px = WORK["px"]
        if not px:
            return None
        st = build_state_from(px, WORK["ver"], work_hist())
        st["age"] = int(time.time() - WORK["t"])
        st["info"] = dict(WORK["info"] or {})
    # рабочий роутер получает те же списки AntiZapret, что и домашний - показываем их и в «Работе»
    st["info"]["lists"] = dict(st["info"].get("lists") or {}, az=az_lists())
    return st


class H(BaseHTTPRequestHandler):
    server_version = "mh"
    sys_version = ""

    def log_message(self, fmt, *a):
        # логируем только API (IP, запрос, статус) - чтобы видеть, что реально приходит с телефона
        try:
            line = fmt % a
            if "/api/" in line:
                ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                sys.stderr.write("api %s %s" % (ip, line) + chr(10))
        except Exception:
            pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "ALLOWALL")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: https://cdn.jsdelivr.net; "
                         "frame-ancestors https://web.telegram.org https://*.telegram.org")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        now = time.time()
        bad = [t for t in _rate.get(ip, []) if now - t < 60]
        if len(bad) >= 20:
            self._send(429, {"error": "too many"})
            return None
        h = self.headers.get("Authorization", "")
        user = verify_init_data(h[4:]) if h.startswith("tma ") else None
        if not user:
            bad.append(now)
            _rate[ip] = bad
            self._send(401, {"error": "unauthorized"})
            return None
        return user

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, open(os.path.join(BASE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif path == "/api/state":
            if not self._auth():
                return
            try:
                self._send(200, build_state())
            except Exception as e:
                self._send(502, {"error": "mihomo недоступен: %s" % type(e).__name__})
        elif path.startswith("/sub/"):
            # только из локальной сети (не через публичный прокси) и по токену
            ip = self.client_address[0]
            try:
                real = open(SUB_TOKEN_FILE, encoding="utf-8").read().strip()
            except Exception:
                real = ""
            if not (ip.startswith("192.168.") or ip.startswith("127.")) or "X-Forwarded-For" in self.headers or not real                     or not hmac.compare_digest(path[5:].encode(), real.encode()):
                return self._send(404, {"error": "not found"})
            try:
                self._send(200, build_sub(), "text/yaml; charset=utf-8")
            except Exception as e:
                self._send(500, {"error": "sub: %s" % type(e).__name__})
        elif path == "/api/rt/lists":
            if not self._auth():
                return
            try:
                self._send(200, rt_lists())
            except Exception as e:
                self._send(502, {"error": "роутер недоступен: %s" % str(e)[:100]})
        elif path == "/api/rt/state":
            if not self._auth():
                return
            try:
                self._send(200, rt_state())
            except Exception as e:
                self._send(502, {"error": "роутер недоступен: %s" % str(e)[:100]})
        elif path == "/api/work/state":
            if not self._auth():
                return
            st = work_state()
            if st is None:
                return self._send(404, {"error": "рабочий роутер ещё не присылал данные"})
            self._send(200, st)
        elif path == "/healthz":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def _agent_ok(self):
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        now = time.time()
        bad = [x for x in _rate.get("agent:" + ip, []) if now - x < 60]
        if len(bad) >= 10:
            self._send(429, {"error": "too many"})
            return False
        k = self.headers.get("X-Agent-Key", "")
        if not AGENT_KEY or not hmac.compare_digest(k.encode(), AGENT_KEY.encode()):
            bad.append(now)
            _rate["agent:" + ip] = bad
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def do_POST(self):
        path = self.path.split("?")[0]
        agent = path in ("/api/agent/report", "/api/work/apply-config")
        if agent:
            if not self._agent_ok():
                return
        elif not self._auth():
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            lim = 800000 if agent else 4096
            body = json.loads(self.rfile.read(n) or b"{}") if 0 < n < lim else {}
        except Exception:
            return self._send(400, {"error": "bad json"})
        if path == "/api/work/apply-config":
            # служебная команда (ключ агента): рабочий роутер скачает конфиг с DE3, проверит и применит
            url = str(body.get("url", ""))
            if not url.startswith("https://de3.hoststatus2346.ru/"):
                return self._send(400, {"error": "url только с de3"})
            with _lock:
                WORK_CMDS.append({"type": "config", "url": url, "t": int(time.time())})
            return self._send(200, {"ok": True, "queued": True})
        if agent:
            try:
                cmds = work_report(body)
            except Exception as e:
                return self._send(400, {"error": str(e)[:100]})
            # долгое ожидание: команды нет - держим запрос агента до 15 с, нажатие в приложении уходит сразу
            end = time.time() + (15 if body.get("lp") else 0)
            while not cmds and time.time() < end:
                time.sleep(0.3)
                with _lock:
                    cmds = list(WORK_CMDS)
                    WORK_CMDS.clear()
            try:
                return self._send(200, {"ok": True, "cmds": cmds})
            except OSError:
                # агент не дождался ответа (обрыв, перезапуск) - команды не теряем, отдадим в следующий раз
                with _lock:
                    WORK_CMDS[:0] = cmds
                raise
        if path == "/api/rt/zapret":
            on = bool(body.get("on"))
            try:
                rt_run("zapret on" if on else "zapret off", timeout=30)
                return self._send(200, rt_state(force=True))
            except Exception as e:
                return self._send(502, {"error": "роутер: %s" % str(e)[:100]})
        if path == "/api/rt/strategy":
            name = str(body.get("name", ""))
            if name not in ("1", "2", "3", "4", "5", "auto"):
                return self._send(400, {"error": "неизвестная стратегия"})
            try:
                rt_run("zapret auto" if name == "auto" else "zapret strategy " + name, timeout=120)
                return self._send(200, rt_state(force=True))
            except Exception as e:
                return self._send(502, {"error": "роутер: %s" % str(e)[:100]})
        if path == "/api/rt/failover":
            cfg = failover_cfg(); cfg["on"] = bool(body.get("on"))
            tmp = FAIL_FILE + ".tmp"
            json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, FAIL_FILE)
            return self._send(200, rt_state(force=True))
        if path == "/api/rt/list":
            act, lst, val = str(body.get("action", "")), str(body.get("list", "")), str(body.get("value", "")).strip()
            if act not in ("add", "del") or lst not in ("vpn", "direct", "block", "noproton") or not re.fullmatch(r"[A-Za-z0-9.*+/-]{1,80}", val):
                return self._send(400, {"error": "некорректный список или значение"})
            try:
                rt_run("list %s %s %s" % (act, lst, val), timeout=30)
                _lists_cache["t"] = 0
                return self._send(200, rt_lists())
            except Exception as e:
                return self._send(502, {"error": "роутер: %s" % str(e)[:100]})
        if path == "/api/rt/awg":
            name = str(body.get("name", ""))
            try:
                known = rt_state()["awgs"]
            except Exception:
                known = []
            if name != "none" and name not in known:
                return self._send(400, {"error": "неизвестный узел"})
            try:
                rt_run("awg " + name, timeout=45)
                return self._send(200, rt_state(force=True))
            except Exception as e:
                return self._send(502, {"error": "роутер: %s" % str(e)[:100]})
        if path == "/api/work/rt":
            cmd = str(body.get("cmd", "")).strip()
            okc = cmd in ("zapret on", "zapret off", "strategy 1", "strategy 2", "strategy 3", "strategy 4", "strategy 5", "strategy 6", "strategy auto") or                 re.fullmatch(r"list (add|del) (vpn|direct|block) ([a-z0-9.-]+\.[a-z0-9-]+|\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?)", cmd.lower())
            if not okc:
                return self._send(400, {"error": "недопустимая команда"})
            with _lock:
                if not WORK["px"] or time.time() - WORK["t"] > 120:
                    return self._send(409, {"error": "рабочий роутер не на связи (нет отчёта больше 2 минут)"})
                WORK_CMDS.append({"type": "rt", "cmd": cmd.lower() if cmd.startswith("list") else cmd, "t": int(time.time())})
            return self._send(200, {"ok": True, "queued": True})
        if path in ("/api/work/select", "/api/work/delay"):
            with _lock:
                px = WORK["px"]
                age = time.time() - WORK["t"]
                if not px or age > 120:
                    return self._send(409, {"error": "рабочий роутер не на связи (нет отчёта больше 2 минут)"})
                if path == "/api/work/delay":
                    if any(c.get("type") == "delay" for c in WORK_CMDS):
                        return self._send(429, {"error": "замер уже поставлен в очередь"})
                    WORK_CMDS.append({"type": "delay", "t": int(time.time())})
                    return self._send(200, {"ok": True, "queued": True})
                group, name = body.get("group"), body.get("name")
                g = px.get(group)
                is_auto = bool(g and g.get("type") in ("URLTest", "Fallback"))
                if not g or not (g.get("type") == "Selector" or is_auto) or group in HIDE_GROUPS                         or not (name in g.get("all", []) or (is_auto and name == "__auto")):
                    return self._send(400, {"error": "недопустимая группа или узел"})
                if len(WORK_CMDS) >= 20:
                    return self._send(429, {"error": "очередь команд переполнена"})
                WORK_CMDS.append({"type": "select", "group": group, "name": name, "t": int(time.time())})
            return self._send(200, {"ok": True, "queued": True})
        if path == "/api/mode":
            mode = body.get("mode")
            if mode not in ("awg2", "awg3"):
                return self._send(400, {"error": "режим awg2 или awg3"})
            px = mihomo("/proxies").get("proxies", {})
            if AUTO2 not in px:
                return self._send(409, {"error": "группа «%s» не настроена" % AUTO2})
            plan = mode_plan(px, mode)
            for g, old_, new_ in plan:
                mihomo("/proxies/" + urllib.parse.quote(g), "PUT", {"name": new_})
            with _lock:
                h = load_hist()
                for g, old_, new_ in plan:
                    h.append({"t": int(time.time()), "group": g, "from": old_, "to": new_, "manual": True})
                save_hist(h)
            return self._send(200, {"ok": True, "changed": len(plan)})
        if path == "/api/work/mode":
            mode = body.get("mode")
            if mode not in ("awg2", "awg3"):
                return self._send(400, {"error": "режим awg2 или awg3"})
            with _lock:
                px = WORK["px"]
                if not px or time.time() - WORK["t"] > 120:
                    return self._send(409, {"error": "рабочий роутер не на связи (нет отчёта больше 2 минут)"})
                if AUTO2 not in px:
                    return self._send(409, {"error": "группа «%s» на рабочем роутере не настроена" % AUTO2})
                plan = mode_plan(px, mode)
                if len(WORK_CMDS) + len(plan) > 40:
                    return self._send(429, {"error": "очередь команд переполнена"})
                for g, old_, new_ in plan:
                    WORK_CMDS.append({"type": "select", "group": g, "name": new_, "t": int(time.time())})
            return self._send(200, {"ok": True, "queued": True, "changed": len(plan)})
        if path == "/api/select":
            group, name = body.get("group"), body.get("name")
            px = mihomo("/proxies").get("proxies", {})
            g = px.get(group)
            is_auto = bool(g) and g.get("type") in ("URLTest", "Fallback")
            if not g or not (g.get("type") == "Selector" or is_auto) or group in HIDE_GROUPS                     or not (name in g.get("all", []) or (is_auto and name == "__auto")):
                return self._send(400, {"error": "недопустимая группа или узел"})
            old = g.get("now")
            if is_auto and name == "__auto":
                mihomo("/proxies/" + urllib.parse.quote(group), "DELETE")
                name = "автовыбор"
            else:
                mihomo("/proxies/" + urllib.parse.quote(group), "PUT", {"name": name})
            with _lock:
                h = load_hist()
                h.append({"t": int(time.time()), "group": group, "from": old, "to": name, "manual": True})
                save_hist(h)
            tg_manual("🏠 ✋ %s: %s → %s" % (group, old or "—", name))
            return self._send(200, {"ok": True})
        if path == "/api/delay":
            global _last_delay_test
            if time.time() - _last_delay_test < 10:
                return self._send(429, {"error": "подождите 10 секунд между замерами"})
            _last_delay_test = time.time()
            q = urllib.parse.urlencode({"url": "http://www.gstatic.com/generate_204", "timeout": 5000})
            try:
                res = mihomo("/group/%s/delay?%s" % (urllib.parse.quote(CHECK), q), timeout=25)
            except Exception as e:
                return self._send(502, {"error": type(e).__name__})
            # группы автовыбора сами пересматривают выбор раз в 5 минут - после замера пересчитать сразу
            # (сначала вложенные, например «Резерв», потом «Авто»; закреплённые вручную не трогаем)
            try:
                for g in urltest_order(mihomo("/proxies", timeout=10).get("proxies", {})):
                    mihomo("/group/%s/delay?%s" % (urllib.parse.quote(g), q), timeout=25)
            except Exception:
                pass
            return self._send(200, {"delays": res})
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    if HOME_MIHOMO:
        threading.Thread(target=poller, daemon=True).start()
    ThreadingHTTPServer((CFG.get("bind", "127.0.0.1"), PORT), H).serve_forever()
