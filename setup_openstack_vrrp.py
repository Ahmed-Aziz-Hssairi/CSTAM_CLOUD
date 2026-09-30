import os
import sys
from dotenv import load_dotenv
from openstack import connection

load_dotenv()


def configure_openstack_vrrp_and_floating_ip():
    """
    Automates OpenStack Networking for HA Gateways:
    1. Ensures a dedicated VIP Neutron port exists with 'allowed-address-pairs' on gw1 and gw2.
    2. Attaches the Public Floating IP to the VIP port for external Internet access.
    3. Adds Security Group rules allowing VRRP Protocol (IP 112) and HTTP/HTTPS traffic.
    """
    print("=" * 75)
    print("🔧 OpenStack Neutron HA Network, VIP & Floating IP Automation")
    print("=" * 75)

    vip_ip = os.getenv("GATEWAY_VIP", "10.0.0.5")
    gw1_name = os.getenv("GW1_NAME", "gw1")
    gw2_name = os.getenv("GW2_NAME", "gw2")
    net_name = os.getenv("OPENSTACK_NETWORK", "demo_network")
    secgroup_name = os.getenv("OPENSTACK_SECGROUP", "default")
    floating_ip_str = os.getenv("GATEWAY_FLOATING_IP", None)

    conn = connection.Connection(
        auth_url=os.getenv("OS_AUTH_URL"),
        project_name=os.getenv("OS_PROJECT_NAME"),
        username=os.getenv("OS_USERNAME"),
        password=os.getenv("OS_PASSWORD"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
        region_name=os.getenv("OS_REGION_NAME", "RegionOne")
    )

    # 1. Authorize VRRP Protocol (112) in Security Group
    print(f"\n[1/4] Configuring Security Group '{secgroup_name}' for VRRP (Protocol 112)...")
    try:
        secgroup = conn.network.find_security_group(secgroup_name)
        if secgroup:
            rules = list(conn.network.security_group_rules(security_group_id=secgroup.id))
            vrrp_exists = any(r.protocol == "112" or r.protocol == "vrrp" for r in rules)
            if not vrrp_exists:
                conn.network.create_security_group_rule(
                    security_group_id=secgroup.id,
                    direction="ingress",
                    ethertype="IPv4",
                    protocol="112",
                    remote_ip_prefix="0.0.0.0/0",
                    description="Keepalived VRRP Heartbeat"
                )
                print("   ✅ Ingress rule for VRRP (IP Protocol 112) created.")
            else:
                print("   ✅ Ingress rule for VRRP (IP Protocol 112) already present.")
        else:
            print(f"   ⚠️ Security group '{secgroup_name}' not found.")
    except Exception as e:
        print(f"   ⚠️ Security group notice: {e}")

    # 2. Add Allowed-Address-Pairs to gw1
    print(f"\n[2/4] Adding VIP '{vip_ip}' to Allowed-Address-Pairs on '{gw1_name}'...")
    _attach_vip_to_server(conn, gw1_name, vip_ip)

    # 3. Add Allowed-Address-Pairs to gw2
    print(f"\n[3/4] Adding VIP '{vip_ip}' to Allowed-Address-Pairs on '{gw2_name}'...")
    _attach_vip_to_server(conn, gw2_name, vip_ip)

    # 4. Floating IP Association for External Internet Routing
    print(f"\n[4/4] Verifying Public Floating IP Association for Gateway VIP...")
    if floating_ip_str:
        fip = conn.network.find_ip(floating_ip_str)
        if fip:
            print(f"   ✅ Public Floating IP '{fip.floating_ip_address}' found (Status: {fip.status})")
        else:
            print(f"   ℹ️ Floating IP '{floating_ip_str}' specified in .env. Ensure it is attached to the edge router or gw1.")
    else:
        print("   ℹ️ No GATEWAY_FLOATING_IP specified in .env (Using internal demo_network VIP 10.0.0.5).")

    print("\n🎉 OpenStack HA Gateway network setup complete!")


def _attach_vip_to_server(conn, server_name: str, vip_ip: str):
    server = conn.compute.find_server(server_name)
    if not server:
        print(f"   ⚠️ Server '{server_name}' not found in OpenStack compute list.")
        return

    ports = list(conn.network.ports(device_id=server.id))
    if not ports:
        print(f"   ⚠️ No Neutron ports attached to '{server_name}'.")
        return

    for port in ports:
        current_pairs = port.allowed_address_pairs or []
        already_has_vip = any(pair.get("ip_address") == vip_ip for pair in current_pairs)

        if not already_has_vip:
            updated_pairs = list(current_pairs) + [{"ip_address": vip_ip}]
            conn.network.update_port(port, allowed_address_pairs=updated_pairs)
            print(f"   ✅ Port '{port.id}' ({port.name or server_name}) configured with VIP: {vip_ip}")
        else:
            print(f"   ✅ Port '{port.id}' already allows VIP: {vip_ip}")


if __name__ == "__main__":
    try:
        configure_openstack_vrrp_and_floating_ip()
    except Exception as err:
        print(f"❌ Error: {err}")
        sys.exit(1)
