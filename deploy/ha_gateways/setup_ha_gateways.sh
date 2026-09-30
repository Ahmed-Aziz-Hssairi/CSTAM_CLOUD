#!/usr/bin/env bash
# ==============================================================================
# FelCloud HA Gateways Setup (Keepalived VRRP + HAProxy)
# Run as root or with sudo on both gw1 and gw2
# Usage:
#   On gw1: sudo ./setup_ha_gateways.sh master
#   On gw2: sudo ./setup_ha_gateways.sh backup
# ==============================================================================

set -e

ROLE=${1:-master}

echo "=== Installing Keepalived and HAProxy ==="
apt-get update && apt-get install -y keepalived haproxy psmisc

# Allow binding to non-local IP (VIP)
echo "net.ipv4.ip_nonlocal_bind=1" >> /etc/sysctl.conf
sysctl -p

# Deploy Keepalived configuration based on role
if [ "$ROLE" == "master" ]; then
    echo "=== Configuring gw1 as VRRP MASTER ==="
    cp keepalived_gw1_master.conf /etc/keepalived/keepalived.conf
else
    echo "=== Configuring gw2 as VRRP BACKUP ==="
    cp keepalived_gw2_backup.conf /etc/keepalived/keepalived.conf
fi

systemctl enable keepalived
systemctl restart keepalived

systemctl enable haproxy
systemctl restart haproxy

echo "=== HA Gateway Setup Completed for role: $ROLE ==="
echo "VIP Status:"
ip addr show | grep 10.0.0.5 || echo "VIP will be assigned based on VRRP priority"
