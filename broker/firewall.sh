#!/bin/sh
# Install at boot and after Docker restarts. Traffic to an address on the host
# traverses INPUT. The broker and app-preview ports share one access rule.
# Accept allowed packets here so a later INPUT rule cannot drop them.
#
# Install this source as root-owned mode 0755 at /etc/loam/broker/firewall.sh.
# check-drift compares the installed bytes and checks both INPUT jumps.
# Keep SUBNET in sync with the broker network configuration.
set -eu

CHAIN=LOAM-BROKER-HOST
SUBNET=172.31.0.0/24
ADDR=172.31.0.1
PORT=8932
PREVIEW_PORT=8935

# Rebuild the chain so reruns replace stale rules and cannot duplicate them.
iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"
# Host clients reach this address on loopback. Containers arrive on the bridge.
iptables -A "$CHAIN" -i lo -j ACCEPT
iptables -A "$CHAIN" -s "$SUBNET" -j ACCEPT
iptables -A "$CHAIN" -j DROP

# One jump into it per port, for packets to the broker's address and that
# port only. Docker inserts its own rules at the top of INPUT on restart, so
# each goes in at position 1 and is re-inserted rather than appended.
for P in "$PORT" "$PREVIEW_PORT"; do
  while iptables -C INPUT -p tcp -d "$ADDR" --dport "$P" -j "$CHAIN" 2>/dev/null; do
    iptables -D INPUT -p tcp -d "$ADDR" --dport "$P" -j "$CHAIN"
  done
  iptables -I INPUT 1 -p tcp -d "$ADDR" --dport "$P" -j "$CHAIN"
done
