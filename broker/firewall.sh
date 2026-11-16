#!/bin/sh
# The wall around the session broker's port. Idempotent; run at boot and
# whenever docker restarts, by loam-broker-firewall.service.
#
# Binding the broker to 172.31.0.1 is not by itself a wall. That address is
# the host's, so every other container on this box -- another app on
# 172.19.x, loam-ui's default network on 172.20.x, anything provisioned later
# -- routes to it perfectly well. Traffic from a container to a host address
# arrives on INPUT, not FORWARD, so DOCKER-USER (where the personas' sight
# wall lives) never sees it and this rule has to be in INPUT.
#
# The rule is source-based and reads in one line: on the broker's port, the
# broker's own bridge is allowed and everything else is dropped. DROP rather
# than REJECT, because the one legitimate client is on the allowed side and a
# scanner on the wrong side should learn nothing.
#
# ACCEPT, not RETURN, for the allowed side. This box's INPUT policy is DROP
# and ufw's chains sit behind this one; a RETURN would hand the allowed
# packet back to INPUT and ufw would drop it a few rules later. Caused
# 2026-09-16: with RETURN, a container on the broker's own network could not
# reach the broker either.
#
# Rendered nowhere: this file IS the source. There is no generator and no
# installer script -- it is copied into place by hand, once, and again after
# any edit here:
#
#   sudo install -o root -g root -m 755 broker/firewall.sh \
#        /etc/loam/broker/firewall.sh
#   sudo systemctl restart loam-broker-firewall.service
#
# What compares them is bin/check-drift (check_broker), two ways. It diffs
# the live copy against this one byte for byte, so an edit here that was
# never installed is a finding. And it asks the KERNEL whether the jump this
# script inserts is in INPUT, built from lib/loam_broker.py's own
# WALL_CHAIN, BIND_ADDR and PORT -- so those three drifting from the module
# is caught by the kernel check rather than by reading a file. SUBNET is the
# one value below that only this file asserts (check-drift compares the
# module's SUBNET against the docker network, not against the rule inside
# the chain), so it is the one to change in both places deliberately.
set -eu

CHAIN=LOAM-BROKER-HOST
SUBNET=172.31.0.0/24
ADDR=172.31.0.1
PORT=8932

# Rebuild the chain from empty every time. Appending to a chain that already
# has the rule is how a wall grows a hundred copies of itself and how a
# changed rule ends up behind its own older version.
iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"
# The box's own processes, which arrive on lo because the address is the
# host's. Not a hole: a process on this box runs as the box's user or root, both
# of whom can read the token file anyway, and bin/broker-probe is one of
# them. Containers never arrive on lo -- they come in on the bridge -- so
# this does not widen the container side by a byte.
iptables -A "$CHAIN" -i lo -j ACCEPT
iptables -A "$CHAIN" -s "$SUBNET" -j ACCEPT
iptables -A "$CHAIN" -j DROP

# One jump into it, for packets to the broker's address and port only. Docker
# inserts its own rules at the top of INPUT on restart, so this goes in at
# position 1 and is re-inserted rather than appended.
while iptables -C INPUT -p tcp -d "$ADDR" --dport "$PORT" -j "$CHAIN" 2>/dev/null; do
  iptables -D INPUT -p tcp -d "$ADDR" --dport "$PORT" -j "$CHAIN"
done
iptables -I INPUT 1 -p tcp -d "$ADDR" --dport "$PORT" -j "$CHAIN"
