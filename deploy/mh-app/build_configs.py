#!/usr/bin/env python3
"""Конфиги mihomo для домашнего и рабочего роутера из одного шаблона и реестра серверов nodes.json.

Шаблон: /opt/mh-app/templates/home.yaml (живой домашний конфиг; правки карточек/правил - здесь).
Реестр: /opt/mh-app/nodes.json (mh-node add/del). Узел с "work": false на работу не идёт.
Выход (только если mihomo -t прошёл): /var/www/az-lists/home-config.yaml и work-config.yaml -
роутеры забирают их сами (config-sync.sh, раз в 30 минут) и применяют без перезапуска.
"""
import json, os, re, subprocess, sys, tempfile

BASE = os.environ.get("MH_APP_BASE", "/opt/mh-app")
OUT = os.environ.get("MH_APP_OUT", "/var/www/az-lists")
GEO = os.environ.get("MH_APP_GEO", "/opt/az-lists/geo")
MIHOMO = os.environ.get("MH_APP_BINARY", "/opt/az-lists/mihomo")
WORK = {"controller": "0.0.0.0:9190", "secret": open(BASE + "/work.secret").read().strip()}
HOME_SECRET = open(BASE + "/mihomo.secret").read().strip()
HOME_SUBSCRIPTION_URL = open(BASE + "/subscription.url").read().strip()
ICON = "https://cdn.jsdelivr.net/gh/Koolson/Qure@master/IconSet/Color/Auto.png"
HEAD = "# Собран на DE3 (/opt/mh-app/build_configs.py) из templates/home.yaml и nodes.json - правьте там, не на роутере.\n"


def group_name(n):
    return "%s %s" % (n["flag"], n["key"])


# MTProxy-домен -> узел (ключ из nodes.json), на котором он стоит
MTPROXY_HOST = {"cdn": "DE1", "animal": "NL2", "static": "LV1", "media": "FI1", "bigboss": "DE4"}
# через какой узел вести MTProxy на работе (замер 23.10: работа->узел + узел->сервер MTProxy); иначе первый рабочий узел
WORK_MTP_VIA = {"static": "FI1", "media": "LV1", "cdn": "FR1"}   # cdn (DE1) через FR1: NL2 с 27.10 заблокирован Fortinet с работы


URL_TEST = {"url": "http://www.gstatic.com/generate_204", "interval": 300, "tolerance": 10, "lazy": False}


def build(tpl, nodes, work):
    # Узлы с "reserve": true (запасные, медленнее основных) не входят в «Авто» по отдельности: они собраны
    # в одну группу-кнопку, которая сама выбирает быстрейший из них, и эта группа - участник «Авто»
    # (выбирается, только если быстрее основных). В карточках сервисов - одна кнопка резерва вместо стран.
    main = [n for n in nodes if not n.get("reserve")]
    res = [n for n in nodes if n.get("reserve")]
    rkeys = "|".join(n["key"] for n in res)
    mkeys = "|".join(n["key"] for n in main)
    res_name = "🌍 Резерв: " + " · ".join(n["key"] for n in res) if res else None
    names = [group_name(n) for n in main] + ([res_name] if res else [])
    out, section, skip = [], None, False
    server_groups_done = False
    old_server = set()
    # имена старых карточек серверов: select из az-vless с фильтром по имени узла
    for line in tpl:
        if re.match(r"^\s+- \{", line):
            try:
                o = json.loads(line.strip()[2:].replace('"🇳🇱 NL2", "🇱🇻 LV1", "type"', '"🇳🇱 NL2", "type"'))
            except ValueError:
                continue
            if o.get("type") == "select" and o.get("use") == ["az-vless"] and o.get("filter") and o["name"] not in ("Все серверы — проверка", "ИИ — ChatGPT и Claude"):
                old_server.add(o["name"])
    for line in tpl:
        m = re.match(r"^([a-z-]+):", line)
        if m:
            section = m.group(1)
        if work and line.startswith("geo-auto-update:"):
            # рабочий роутер 256 МБ: скачивание GeoSite (~22 МБ целиком в память) роняет mihomo (OOM 23.10)
            out.append("geo-auto-update: false"); continue
        if work and line.startswith("external-controller:"):
            out.append("external-controller: " + WORK["controller"]); continue
        if work and line.startswith("secret:"):
            out.append('secret: "%s"' % WORK["secret"]); continue
        if work and section == "dns" and line.strip().startswith('"+.youtube.com,'):
            # YouTube тоже через DoH: 62.76.76.62 на работе перехватывается (для yt3.ggpht.com - мёртвый адрес)
            out.append(re.sub(r"\[[^\]]*\]", "[\"https://1.1.1.1/dns-query\", \"https://1.0.0.1/dns-query\", \"https://8.8.8.8/dns-query\"]", line))
            out.append('    "+.netcraze.link,+.netcraze.io,+.keenetic.link,+.keenetic.net,+.keenetic.pro": [192.168.1.1]')
            continue
        if work and section == "proxy-providers" and line.strip().startswith("url:") and "/sub/" in line:
            out.append(line)
            excl = [n["key"] for n in NODES_ALL if n not in nodes]
            if excl:
                out.append('    exclude-filter: "%s"' % "|".join(excl))
            continue
        if section == "proxy-groups" and re.match(r"^\s+- \{", line):
            o = json.loads(line.strip()[2:].replace('"🇳🇱 NL2", "🇱🇻 LV1", "type"', '"🇳🇱 NL2", "type"'))
            if o["name"] in old_server:
                if not server_groups_done:   # на месте первой старой карточки сервера - все актуальные
                    for n in nodes:
                        g = {"name": group_name(n), "type": "select", "use": ["az-vless"], "filter": n["key"], "icon": ICON}
                        out.append("  - " + json.dumps(g, ensure_ascii=False))
                    server_groups_done = True
                continue
            if o["name"] in ("Авто — самый быстрый", "Авто — без Proton") and mkeys:
                kind = "Proton" if "быстрый" in o["name"] else "прямой"
                o["filter"] = "(%s) \\| VLESS %s" % (mkeys, kind)    # основные узлы
                npk = [n["key"] for n in main if not n.get("port_proton")]   # узлы без Proton: в «самый быстрый» идут прямым вариантом
                if kind == "Proton" and npk:
                    o["filter"] += "|(%s) \\| VLESS прямой" % "|".join(npk)
                if res:
                    o["proxies"] = [res_name]                          # + одна карточка резерва
                out.append("  - " + json.dumps(o, ensure_ascii=False))
                if res and kind == "прямой":       # карточка резерва: быстрейшая из карточек запасных серверов
                    out.append("  - " + json.dumps(dict({"name": res_name, "type": "url-test",
                                                         "proxies": [group_name(n) for n in res], "icon": ICON}, **URL_TEST), ensure_ascii=False))
                continue
            if "proxies" in o and any(p in old_server for p in o["proxies"]):
                new, put = [], False
                for p in o["proxies"]:
                    if p in old_server:
                        if not put:
                            new += names; put = True
                    else:
                        new.append(p)
                o["proxies"] = new
            out.append("  - " + json.dumps(o, ensure_ascii=False)); continue
        # работа: Fortinet перехватывает прямые DNS-запросы (62.76.76.62, 77.88.8.8) и подсовывает страницу блокировки -
        # все прямые DNS mihomo - Cloudflare по DoH (Fortinet его не трогает, от туннеля Кинетика не зависит); запросы через «Авто» (Cloudflare по VPN) не трогаем
        if work and section == "dns" and re.search(r"62\.76\.76\.62|77\.88\.8\.8", line) and "#" not in line.split(":", 1)[0]:
            line = re.sub(r"\[[^\]]*\]", "[\"https://1.1.1.1/dns-query\", \"https://1.0.0.1/dns-query\", \"https://8.8.8.8/dns-query\"]", line)
        if work and section == "rule-providers":
            mm = re.match(r"^  ([a-z0-9-]+):", line)
            if mm:
                skip = False    # az-full на работе с 23.10 (подкачка на флешке, GOMEMLIMIT 140MiB); было: не влезал в 256 МБ
            if skip:
                continue
            # 23.10 Fortinet на работе режет TLS к *.hoststatus2346.ru (DE3) - списки с DE3 качаем через VPN
            if line.startswith('    url: "https://de3.hoststatus2346.ru/lists/'):
                out.append(line)
                out.append('    proxy: "Авто — самый быстрый"')
                continue
        # на работе Fortinet режет домены MTProxy (DNS-блок + обрыв после TLS) - MTProxy через VPN.
        # Сервер не может подключиться сам к себе (ни напрямую, ни через свой Proton), поэтому домен MTProxy,
        # который стоит на одном из рабочих узлов, ведём через другой узел.
        # через прямой VLESS (не Proton): выход Proton до части наших серверов не доходит
        # (правила не могут ссылаться на сервер из подписки - цель скрытая группа «MTP · KEY», см. ниже).
        # Ставим ДО my-noproton/my-vpn/my-vpn-ips: в рабочем my-vpn-ips лежат IP всех наших серверов.
        if work and section == "rules" and line.strip().startswith("- RULE-SET,my-noproton,"):
            for sub, host in MTPROXY_HOST.items():
                via = next((n for n in nodes if n["key"] == WORK_MTP_VIA.get(sub) and n["key"] != host), None) \
                    or next(n for n in nodes if n["key"] != host)
                out.append("  - DOMAIN,%s.hoststatus2343.ru,MTP · %s" % (sub, via["key"]))
            out.append("  - DOMAIN-SUFFIX,hoststatus2343.ru,MTP · %s" % nodes[0]["key"])
        if work and section == "rules" and line.strip() == "- DOMAIN-SUFFIX,hoststatus2343.ru,Напрямую (провайдер)":
            continue
        out.append(line)
    text = "\n".join(out)
    if work:  # скрытые группы для MTProxy на работе: только прямой VLESS одного узла
        groups = "".join('  - {"name": "MTP · %s", "type": "select", "use": ["az-vless"], "filter": "^%s %s \\\\| VLESS прямой$", "hidden": true}\n'
                         % (n["key"], n["flag"], n["key"]) for n in nodes)
        text = text.replace("\nproxy-groups:\n", "\nproxy-groups:\n" + groups, 1)
    text = re.sub(r"^# Собран на DE3.*\n", "", text)
    text = text.replace("__MH_HOME_SECRET__", HOME_SECRET)
    text = text.replace("__MH_SUBSCRIPTION_URL__", HOME_SUBSCRIPTION_URL)
    return HEAD + text


def check(text):
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(d + "/providers")
        for f in ("GeoSite.dat", "GeoIP.dat"):
            os.symlink(os.path.join(GEO, f), os.path.join(d, f))
        for f in ("my-block", "my-block-ips", "my-direct", "my-direct-ips", "my-vpn", "my-vpn-ips", "my-noproton"):
            open("%s/providers/%s.txt" % (d, f), "w").close()
        open(d + "/config.yaml", "w").write(text)
        r = subprocess.run([MIHOMO, "-d", d, "-t", "-f", d + "/config.yaml"], capture_output=True, text=True, timeout=180)
        return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:]


def publish(name, text):
    ok, msg = check(text)
    if not ok:
        print("%s: ПРОВЕРКА НЕ ПРОШЛА, не публикую: %s" % (name, msg)); return False
    path = os.path.join(OUT, name)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != text:
        tmp = path + ".tmp"
        open(tmp, "w", encoding="utf-8").write(text); os.chmod(tmp, 0o644); os.replace(tmp, path)
        print("%s: обновлён" % name)
    else:
        print("%s: без изменений" % name)
    return True


NODES_ALL = json.load(open(BASE + "/nodes.json", encoding="utf-8"))["nodes"]
if __name__ == "__main__":
    tpl = open(BASE + "/templates/home.yaml", encoding="utf-8").read().split("\n")
    ok = publish("home-config.yaml", build(tpl, NODES_ALL, False))
    ok = publish("work-config.yaml", build(tpl, [n for n in NODES_ALL if n.get("work", True)], True)) and ok
    sys.exit(0 if ok else 1)
