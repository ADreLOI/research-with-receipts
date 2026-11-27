#!/usr/bin/env bash
# Egress guard for sandbox containers: drop NEW connections from containers to
# cloud metadata (169.254.0.0/16) and private / carrier-grade NAT ranges.
# Idempotent. Run by rwr-egress-guard.service every time Docker (re)starts,
# because Docker rebuilds its chains and the rules are not saved otherwise.
set -euo pipefail

# Docker creates the DOCKER-USER chain when it starts; wait for it.
for _ in $(seq 1 30); do
  iptables -nL DOCKER-USER >/dev/null 2>&1 && break
  sleep 1
done
iptables -nL DOCKER-USER >/dev/null 2>&1 || { echo "DOCKER-USER chain missing; is Docker running?" >&2; exit 1; }

for net in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
  iptables -C DOCKER-USER -d "$net" -m conntrack --ctstate NEW -j DROP 2>/dev/null \
    || iptables -I DOCKER-USER -d "$net" -m conntrack --ctstate NEW -j DROP
done
# Replies to connections that were allowed stay allowed (inserted last, so it is first).
iptables -C DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
echo "egress guard active:"
iptables -nL DOCKER-USER --line-numbers
