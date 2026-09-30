import os
from dotenv import load_dotenv
from openstack import connection

load_dotenv()


def main():
    try:
        conn = connection.Connection(
            auth_url=os.getenv("OS_AUTH_URL"),
            project_name=os.getenv("OS_PROJECT_NAME"),
            username=os.getenv("OS_USERNAME"),
            password=os.getenv("OS_PASSWORD"),
            user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
            project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
            region_name=os.getenv("OS_REGION_NAME", "North-Africa")
        )
        print("Authenticating with Keystone...")
        auth_data = conn.session.auth.get_access(conn.session)
        print(" Keystone authentication SUCCESSFUL!")
        print(f"Logged in user: {os.getenv('OS_USERNAME')}")
        print(f"Project: {os.getenv('OS_PROJECT_NAME')}\n")

        print("=== Available OpenStack Services in Catalog ===")
        catalog = auth_data.service_catalog.catalog
        dns_service_present = False
        for svc in catalog:
            svc_type = svc.get("type", "unknown")
            svc_name = svc.get("name", "unknown")
            endpoints = [f"{e.get('interface')}: {e.get('url')}" for e in svc.get("endpoints", [])]
            print(f"- Type: {svc_type:<15} | Name: {svc_name:<15} | Endpoints: {endpoints}")
            if svc_type in ["dns", "dns-designate"]:
                dns_service_present = True

        print("\n=== Testing Compute (Nova) & Network (Neutron) ===")
        try:
            flavors = list(conn.compute.flavors())
            print(f"[OK] Compute (Nova) connected! Found {len(flavors)} flavors.")
        except Exception as e:
            print(f"[ERROR] Nova check: {e}")

        try:
            networks = list(conn.network.networks())
            print(f"[OK] Network (Neutron) connected! Found {len(networks)} networks:")
            for n in networks:
                print(f"   - Network: {n.name} (ID: {n.id})")
        except Exception as e:
            print(f"[ERROR] Neutron check: {e}")

        print("\n=== Testing DNS Layer ===")
        if not dns_service_present:
            print("[INFO] OpenStack Designate (DNS-as-a-Service) is not in the cloud catalog.")
            print("       The FelCloud DNS Service is running in HA Gateway mode (Local/VRRP).")
        else:
            try:
                zones = list(conn.dns.zones())
                print(f"[OK] DNS connected! Found {len(zones)} zones:")
                for z in zones:
                    print(f"   - Zone: {z.name} (ID: {z.id})")
            except Exception as e:
                print(f"[ERROR] DNS check: {e}")


    except Exception as e:
        print(f"Error during OpenStack connection: {e}")


if __name__ == "__main__":
    main()

