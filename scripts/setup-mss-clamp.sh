#!/usr/bin/env bash
# MSS-кламп на исходящих SYN-ACK с tcp/443 (+ tcp_mtu_probing) для узлов с MTProxy/Telegram.
# Зачем: клиенты Tele2 (464XLAT) шлют ClientHello полным MSS, продолжение теряется на пути,
# и telemt пишет «Telegram handshake timeout». Сервер объявляет mss 1200 — клиент режет сегменты.
# Идемпотентно: повторный запуск перезаписывает файлы и перезапускает unit.
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Нужен root" >&2; exit 1; }
[[ "${MSS_CLAMP_DISABLE:-0}" == "1" ]] && exit 0
MSS="${MSS_CLAMP_VALUE:-1200}"
[[ "$MSS" =~ ^[0-9]{3,4}$ ]] || { echo "MSS_CLAMP_VALUE некорректен: $MSS" >&2; exit 1; }

if ! command -v nft >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nftables
fi

cat > /etc/mss-clamp.nft <<EOF
add table inet mssx
delete table inet mssx
table inet mssx {
  chain post {
    type filter hook postrouting priority mangle; policy accept;
    tcp sport 443 tcp flags & (syn | ack) == (syn | ack) tcp option maxseg size set ${MSS}
  }
}
EOF

cat > /etc/systemd/system/mss-clamp.service <<'EOF'
[Unit]
Description=Clamp MSS on tcp/443 SYN-ACK (Tele2 464XLAT)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/mss-clamp.nft
ExecStop=/usr/sbin/nft delete table inet mssx

[Install]
WantedBy=multi-user.target
EOF

printf 'net.ipv4.tcp_mtu_probing=1\n' > /etc/sysctl.d/99-mtu-probing.conf
sysctl -q -p /etc/sysctl.d/99-mtu-probing.conf

systemctl daemon-reload
systemctl enable mss-clamp.service >/dev/null
systemctl restart mss-clamp.service

nft list table inet mssx | grep -q "maxseg size set ${MSS}" \
  || { echo "mss-clamp: правило не появилось в nft" >&2; exit 1; }
echo "mss-clamp: tcp/443 SYN-ACK -> mss ${MSS}, tcp_mtu_probing=1"
