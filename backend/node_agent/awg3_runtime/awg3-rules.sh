#!/bin/sh
# AmneziaWG 3.1 (interface awg1) firewall rules.
#   split 10.9.0.0/24 - antizapret: mirrors the 10.29 rules (DNS -> 127.1.1.1, connmark, drop not in antizapret-forward, WARP per ANTIZAPRET_WARP)
#   full  10.9.1.0/24 - whole-traffic VPN: mirrors the 10.28 rules (DNS -> 127.2.2.2, no drop, WARP per VPN_WARP)
# usage: awg3-rules.sh up|down
# WARP: без SNAT на warp-интерфейс провайдер не принимает трафик с адресом клиента,
# поэтому правила ip rule и SNAT добавляются вместе (как в up.sh для 10.29 и 10.28).
[ -f /root/antizapret/setup ] && . /root/antizapret/setup
S=10.9.0.0/24
F=10.9.1.0/24
OUT=$(ip route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
if [ "$WARP_PROVIDER" = "proton" ]; then
  AZ_WARP_IP="$PROTON_ANTIZAPRET_ADDRESS"
  VPN_WARP_IP="$PROTON_VPN_ADDRESS"
else
  AZ_WARP_IP="${ANTIZAPRET_WARP_ADDRESS%%/*}"
  VPN_WARP_IP="${VPN_WARP_ADDRESS%%/*}"
fi
add(){ t=$1; shift; iptables -w -t $t -C "$@" 2>/dev/null || iptables -w -t $t -A "$@"; }
del(){ t=$1; shift; while iptables -w -t $t -C "$@" 2>/dev/null; do iptables -w -t $t -D "$@"; done; }
rule_add(){ ip rule del "$@" 2>/dev/null; ip rule add "$@" || true; }
rule_del(){ ip rule del "$@" 2>/dev/null || true; }
# удаляет ВСЕ nat-правила подсети на warp-интерфейс, какой бы адрес в них ни стоял
# (после смены ключа Proton адрес другой, и удаление по "--to-source <новый адрес>" старое правило не находило)
purge_warp_nat(){ iptables -w -t nat -S POSTROUTING | grep -F -- "-s $1 " | grep -F -- "-o $2 " | sed 's/^-A /-D /' | while read -r r; do iptables -w -t nat $r; done; }
# SNAT на WARP-интерфейс: адрес провайдера, либо MASQUERADE если адрес не задан (как в up.sh)
warp_snat(){ src=$1; iface=$2; ip=$3; shift 3; if [ -z "$ip" ]; then add nat POSTROUTING -s $src "$@" -o $iface -j MASQUERADE; else add nat POSTROUTING -s $src "$@" -o $iface -j SNAT --to-source $ip; fi; }
# client ports 51900-51999 (random per client, see awg3_clients.py) -> ListenPort 51821
if [ "$1" = "down" ]; then
  del nat PREROUTING -i $OUT -p udp --dport 51900:51999 -j REDIRECT --to-ports 51821
  del nat PREROUTING -s $S -p udp --dport 53 -j DNAT --to-destination 127.1.1.1
  del nat PREROUTING -s $S -p tcp --dport 53 -j DNAT --to-destination 127.1.1.1
  del nat PREROUTING -s $S ! -d 198.18.0.0/15 -j CONNMARK --set-xmark 0x1/0xffffffff
  del nat PREROUTING -s $S -d 198.18.0.0/15 -j ANTIZAPRET-MAPPING
  del filter FORWARD -s $S -m connmark --mark 0x1 -m set ! --match-set antizapret-forward dst -j DROP
  del mangle FORWARD -s $S -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
  del mangle PREROUTING -s $S -d 198.18.0.0/15 -j ANTIZAPRET-WARP
  del nat POSTROUTING -s $S -o $OUT -j MASQUERADE
  purge_warp_nat $S warp-antizapret
  rule_del from $S to $S lookup main priority 5000
  rule_del from $S lookup 13335 priority 10000
  rule_del from $S fwmark 0x2 lookup 13335 priority 10000
  del nat PREROUTING -s $F -p udp --dport 53 -j DNAT --to-destination 127.2.2.2
  del nat PREROUTING -s $F -p tcp --dport 53 -j DNAT --to-destination 127.2.2.2
  del nat PREROUTING -s $F -d 198.18.0.0/15 -j ANTIZAPRET-MAPPING
  del mangle FORWARD -s $F -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
  del nat POSTROUTING -s $F -o $OUT -j MASQUERADE
  purge_warp_nat $F warp-vpn
  rule_del from $F to $F lookup main priority 5000
  rule_del from $F lookup 13336 priority 10000
  exit 0
fi
add nat PREROUTING -i $OUT -p udp --dport 51900:51999 -j REDIRECT --to-ports 51821
# split
add nat PREROUTING -s $S -p udp --dport 53 -j DNAT --to-destination 127.1.1.1
add nat PREROUTING -s $S -p tcp --dport 53 -j DNAT --to-destination 127.1.1.1
add nat PREROUTING -s $S ! -d 198.18.0.0/15 -j CONNMARK --set-xmark 0x1/0xffffffff
add nat PREROUTING -s $S -d 198.18.0.0/15 -j ANTIZAPRET-MAPPING
iptables -w -C FORWARD -s $S -m connmark --mark 0x1 -m set ! --match-set antizapret-forward dst -j DROP 2>/dev/null || iptables -w -I FORWARD 2 -s $S -m connmark --mark 0x1 -m set ! --match-set antizapret-forward dst -j DROP
add mangle FORWARD -s $S -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
add nat POSTROUTING -s $S -o $OUT -j MASQUERADE
# policy routing and WARP for split: same as 10.29 in up.sh (ANTIZAPRET_WARP 2 = all, 3/4 = only marked fake-IP traffic)
rule_add from $S to $S lookup main priority 5000
# сначала убираем WARP-правила любого режима: после смены ANTIZAPRET_WARP старые остались бы рядом с новыми
rule_del from $S lookup 13335 priority 10000
rule_del from $S fwmark 0x2 lookup 13335 priority 10000
del filter FORWARD -s $S -m mark --mark 0x2 ! -o warp-antizapret -j DROP
purge_warp_nat $S warp-antizapret
del mangle PREROUTING -s $S -d 198.18.0.0/15 -j ANTIZAPRET-WARP
case "$ANTIZAPRET_WARP" in
  2)
    rule_add from $S lookup 13335 priority 10000
    warp_snat $S warp-antizapret "$AZ_WARP_IP"
    ;;
  3|4)
    rule_add from $S fwmark 0x2 lookup 13335 priority 10000
    add mangle PREROUTING -s $S -d 198.18.0.0/15 -j ANTIZAPRET-WARP
    warp_snat $S warp-antizapret "$AZ_WARP_IP" -m mark --mark 0x2
    # как в up.sh для 10.29: помеченный пакет, не ушедший в WARP, режем, а не отдаём в main
    iptables -w -C FORWARD -s $S -m mark --mark 0x2 ! -o warp-antizapret -j DROP 2>/dev/null || iptables -w -I FORWARD 2 -s $S -m mark --mark 0x2 ! -o warp-antizapret -j DROP
    ;;
esac
# full
add nat PREROUTING -s $F -p udp --dport 53 -j DNAT --to-destination 127.2.2.2
add nat PREROUTING -s $F -p tcp --dport 53 -j DNAT --to-destination 127.2.2.2
add nat PREROUTING -s $F -d 198.18.0.0/15 -j ANTIZAPRET-MAPPING
add mangle FORWARD -s $F -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
add nat POSTROUTING -s $F -o $OUT -j MASQUERADE
# policy routing and WARP for full: same as 10.28 in up.sh (VPN_WARP 2 = all)
rule_add from $F to $F lookup main priority 5000
rule_del from $F lookup 13336 priority 10000
purge_warp_nat $F warp-vpn
del filter FORWARD -s $F -m mark --mark 0x2 ! -o warp-vpn -j DROP
if [ "$VPN_WARP" = "2" ]; then
  rule_add from $F lookup 13336 priority 10000
  warp_snat $F warp-vpn "$VPN_WARP_IP"
fi
if [ "$VPN_WARP" = "3" ]; then
  iptables -w -C FORWARD -s $F -m mark --mark 0x2 ! -o warp-vpn -j DROP 2>/dev/null || iptables -w -I FORWARD 2 -s $F -m mark --mark 0x2 ! -o warp-vpn -j DROP
fi
# как у AWG 2 (up.sh): IP из antizapret-drop не отдаём ни одной подсети AWG 3
for C in $S $F; do
  iptables -w -C FORWARD -s $C -m set --match-set antizapret-drop dst -j DROP 2>/dev/null || iptables -w -I FORWARD 2 -s $C -m set --match-set antizapret-drop dst -j DROP
done
