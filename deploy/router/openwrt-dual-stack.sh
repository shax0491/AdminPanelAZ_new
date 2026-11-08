#!/bin/sh
set -eu

IPV6_MODE="${IPV6_MODE:-a}"
TUNNEL_DEVICE="${TUNNEL_DEVICE:-awg0}"
DNS_PORT="${DNS_PORT:-7874}"
WAN_DEVICE="$(ip -4 route show default | awk 'NR==1 {print $5}')"
[ -n "$WAN_DEVICE" ] || { echo 'No IPv4 default route' >&2; exit 1; }
case "$IPV6_MODE" in a|b) ;; *) echo 'IPV6_MODE must be a or b' >&2; exit 2;; esac
case "$TUNNEL_DEVICE" in *[!A-Za-z0-9_.-]*) echo 'Invalid tunnel device' >&2; exit 2;; esac

command -v uci >/dev/null
command -v nft >/dev/null

uci -q set network.wan6=interface
uci -q set network.wan6.proto=dhcpv6
uci -q set network.wan6.device="$WAN_DEVICE"
uci -q set network.wan6.reqaddress=try
uci -q set network.wan6.reqprefix=auto
uci -q set network.wan6.delegate=1
uci -q set network.wan6.nodns=1

uci -q set firewall.azvpn=zone
uci -q set firewall.azvpn.name=azvpn
uci -q set firewall.azvpn.input=ACCEPT
uci -q set firewall.azvpn.output=ACCEPT
uci -q set firewall.azvpn.forward=ACCEPT
uci -q set firewall.azvpn.masq=1
uci -q set firewall.azvpn.masq6=1
uci -q set firewall.azvpn.network="$TUNNEL_DEVICE"

for family in ipv4 ipv6; do
  uci -q set "firewall.az_dns_${family}=redirect"
  uci -q set "firewall.az_dns_${family}.name=AZ-DNS-${family}"
  uci -q set "firewall.az_dns_${family}.src=lan"
  uci -q set "firewall.az_dns_${family}.proto=tcp udp"
  uci -q set "firewall.az_dns_${family}.src_dport=53"
  uci -q set "firewall.az_dns_${family}.dest_port=$DNS_PORT"
  uci -q set "firewall.az_dns_${family}.target=DNAT"
  uci -q set "firewall.az_dns_${family}.family=$family"
done

uci -q set firewall.az_quic=rule
uci -q set firewall.az_quic.name=AZ-QUIC-Tunnel-Fallback
uci -q set firewall.az_quic.src=azvpn
uci -q set firewall.az_quic.proto=udp
uci -q set firewall.az_quic.dest_port=443
uci -q set firewall.az_quic.target=DROP

uci commit network
uci commit firewall

mkdir -p /etc/nftables.d
cat > /etc/nftables.d/99-az-dual-stack.nft <<EOF
table inet az_dual_stack {
  set doh4 { type ipv4_addr; elements = { 1.1.1.1, 1.0.0.1, 8.8.8.8, 8.8.4.4, 9.9.9.9, 149.112.112.112 } }
  set doh6 { type ipv6_addr; elements = { 2606:4700:4700::1111, 2606:4700:4700::1001, 2001:4860:4860::8888, 2001:4860:4860::8844 } }
  chain forward {
    type filter hook forward priority 10; policy accept;
    oifname "$WAN_DEVICE" ip daddr @doh4 tcp dport 443 drop
    oifname "$WAN_DEVICE" ip6 daddr @doh6 tcp dport 443 drop
  }
}
EOF

nft -c -f /etc/nftables.d/99-az-dual-stack.nft
fw4 check
/etc/init.d/network reload
/etc/init.d/firewall reload

if [ "$IPV6_MODE" = b ]; then
  sysctl -w net.ipv6.conf.all.forwarding=1
  ip6tables -t nat -C POSTROUTING -o "$TUNNEL_DEVICE" -j MASQUERADE 2>/dev/null || \
    ip6tables -t nat -A POSTROUTING -o "$TUNNEL_DEVICE" -j MASQUERADE
fi
