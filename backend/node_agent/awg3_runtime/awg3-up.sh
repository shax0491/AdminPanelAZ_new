#!/bin/sh
# usage: awg3-up.sh <iface>   |   awg3-up.sh down <iface>
if [ "$1" = "down" ]; then ip link del "$2" 2>/dev/null || true; /usr/local/sbin/awg3-rules.sh down; exit 0; fi
I=$1; C=/etc/amnezia/amneziawg3/$I.conf
# Модуль ядра amneziawg (awg-kmod.sh) быстрее; без него (ядро, под которое модуль не собрался) - amneziawg-go
ip link show "$I" >/dev/null 2>&1 || ip link add "$I" type amneziawg 2>/dev/null || amneziawg-go "$I"
grep -vE "^(Address|MTU) " "$C" > /run/$I.setconf; chmod 600 /run/$I.setconf
awg setconf "$I" /run/$I.setconf; rm -f /run/$I.setconf
ip addr replace 10.9.0.1/24 dev "$I"
ip addr replace 10.9.1.1/24 dev "$I"
MTU=$(cat /etc/amnezia/amneziawg3/mtu 2>/dev/null || echo 1280)
case $MTU in ''|*[!0-9]*) MTU=1280 ;; esac
ip link set "$I" mtu "$MTU" up
/usr/local/sbin/awg3-rules.sh up
