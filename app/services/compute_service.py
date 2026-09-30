import os
import time
from typing import Optional, Dict, List
from dotenv import load_dotenv
from openstack import connection

load_dotenv()


class ComputeService:
    """
    OpenStack Nova & Neutron Compute Provisioning Service.
    Automates provisioning and teardown of student VMs with dynamically assigned private IPs.
    """

    def __init__(self):
        self.auth_url = os.getenv("OS_AUTH_URL")
        self.project_name = os.getenv("OS_PROJECT_NAME")
        self.username = os.getenv("OS_USERNAME")
        self.password = os.getenv("OS_PASSWORD")
        self.user_domain_name = os.getenv("OS_USER_DOMAIN_NAME", "Default")
        self.project_domain_name = os.getenv("OS_PROJECT_DOMAIN_NAME", "Default")
        self.region_name = os.getenv("OS_REGION_NAME", "North-Africa")
        
        self.default_network = os.getenv("OPENSTACK_NETWORK", "demo_network")
        self.default_flavor = os.getenv("OPENSTACK_FLAVOR", "G0.basic.1c1g")
        self.default_image = os.getenv("OPENSTACK_IMAGE", "CirrOS 0.6.3")
        self.default_key_name = os.getenv("OPENSTACK_KEY_NAME", None)
        self.default_secgroup = os.getenv("OPENSTACK_SECGROUP", "default")

        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = connection.Connection(
                auth_url=self.auth_url,
                project_name=self.project_name,
                username=self.username,
                password=self.password,
                user_domain_name=self.user_domain_name,
                project_domain_name=self.project_domain_name,
                region_name=self.region_name
            )
        return self._conn

    def _resolve_image(self, img_name: Optional[str] = None):
        """Resolves image by exact match, case-insensitive match, or substring match."""
        images = list(self.conn.image.images())
        if not images:
            raise Exception("No images found in OpenStack Glance.")

        if img_name:
            target = img_name.strip().lower()
            for img in images:
                if img.id == img_name:
                    return img
            for img in images:
                if img.name and img.name.lower() == target:
                    return img
            for img in images:
                if img.name and target in img.name.lower():
                    return img

        # Fallback: CirrOS or first active image
        for img in images:
            if img.name and "cirros" in img.name.lower():
                return img
        return images[0]

    def _resolve_flavor(self, flav_name: Optional[str] = None):
        """Resolves flavor by exact match, case-insensitive match, or smallest available."""
        flavors = list(self.conn.compute.flavors())
        if not flavors:
            raise Exception("No flavors found in OpenStack Nova.")

        if flav_name:
            target = flav_name.strip().lower()
            for f in flavors:
                if f.id == flav_name or (f.name and f.name.lower() == target):
                    return f
            for f in flavors:
                if f.name and target in f.name.lower():
                    return f

        # Fallback: G0.basic.1c1g or smallest flavor
        for f in flavors:
            if f.name and "g0.basic.1c1g" in f.name.lower():
                return f
        flavors.sort(key=lambda x: x.ram or 999999)
        return flavors[0]

    def create_sandbox_vm(
        self,
        team_name: str,
        ip_address: str,
        image_name: Optional[str] = None,
        flavor_name: Optional[str] = None,
        network_name: Optional[str] = None,
        wait_active: bool = False,
        timeout: int = 180
    ) -> Dict:
        """
        Provisions a student VM on OpenStack attached to a specific fixed private IP.
        """
        vm_name = f"sbx-{team_name.strip().lower()}"
        net_name = network_name or self.default_network
        img_name = image_name or self.default_image
        flav_name = flavor_name or self.default_flavor

        # 1. Resolve Network
        network = self.conn.network.find_network(net_name)
        if not network:
            raise Exception(f"OpenStack network '{net_name}' not found.")

        # 2. Resolve Image & Flavor
        image = self._resolve_image(img_name)
        flavor = self._resolve_flavor(flav_name)

        # 3. Create Neutron Port with assigned Fixed IP if supported, or attach to network
        port_name = f"port-{vm_name}"
        existing_port = self.conn.network.find_port(port_name)
        if existing_port:
            port = existing_port
        else:
            try:
                # Find subnet for network
                subnets = list(self.conn.network.subnets(network_id=network.id))
                subnet_id = subnets[0].id if subnets else None
                fixed_ips = [{"ip_address": ip_address}]
                if subnet_id:
                    fixed_ips[0]["subnet_id"] = subnet_id

                port = self.conn.network.create_port(
                    name=port_name,
                    network_id=network.id,
                    fixed_ips=fixed_ips,
                    description=f"Auto-provisioned port for team {team_name}"
                )
            except Exception as e:
                # If specific fixed_ip port creation is restricted, attach via network direct
                port = None

        # 4. Check if server already exists
        existing_server = self.conn.compute.find_server(vm_name)
        if existing_server:
            return {
                "server_id": existing_server.id,
                "server_name": existing_server.name,
                "status": existing_server.status,
                "ip_address": ip_address,
                "action": "already_exists"
            }

        # 5. Build network attachments
        if port:
            networks = [{"port": port.id}]
        else:
            networks = [{"uuid": network.id, "fixed_ip": ip_address}]

        server_kwargs = {
            "name": vm_name,
            "image_id": image.id,
            "flavor_id": flavor.id,
            "networks": networks,
            "metadata": {
                "team": team_name,
                "role": "student_sandbox",
                "ipam_ip": ip_address
            }
        }
        if self.default_key_name:
            server_kwargs["key_name"] = self.default_key_name

        server = self.conn.compute.create_server(**server_kwargs)

        if wait_active:
            server = self.conn.compute.wait_for_server(server, status="ACTIVE", failures=["ERROR"], wait=timeout)

        return {
            "server_id": server.id,
            "server_name": server.name,
            "status": getattr(server, "status", "BUILD"),
            "team_name": team_name,
            "ip_address": ip_address,
            "flavor": flav_name,
            "image": img_name,
            "action": "provisioned"
        }

    def delete_sandbox_vm(self, team_name: str, delete_port: bool = True) -> Dict:
        """
        Teardown VM and its associated Neutron resources.
        """
        vm_name = f"sbx-{team_name.strip().lower()}"
        server = self.conn.compute.find_server(vm_name)

        if not server:
            return {
                "action": "not_found",
                "server_name": vm_name,
                "team_name": team_name
            }

        server_id = server.id
        self.conn.compute.delete_server(server)

        # Cleanup dedicated port if existed
        if delete_port:
            port_name = f"port-{vm_name}"
            port = self.conn.network.find_port(port_name)
            if port:
                try:
                    time.sleep(1)
                    self.conn.network.delete_port(port)
                except Exception:
                    pass

        return {
            "action": "deleted",
            "server_id": server_id,
            "server_name": vm_name,
            "team_name": team_name
        }

    def get_sandbox_vm(self, team_name: str) -> Optional[Dict]:
        """Returns details of a student sandbox VM."""
        vm_name = f"sbx-{team_name.strip().lower()}"
        server = self.conn.compute.find_server(vm_name)
        if not server:
            return None

        # Extract IPs from server addresses dict
        addresses = server.addresses if hasattr(server, "addresses") else {}
        ip_list = []
        for net_name, addr_objs in addresses.items():
            for a in addr_objs:
                ip_list.append(a.get("addr"))

        return {
            "server_id": server.id,
            "server_name": server.name,
            "status": server.status,
            "addresses": ip_list,
            "metadata": server.metadata,
            "created_at": server.created_at
        }

    def list_sandbox_vms(self, include_all: bool = True) -> List[Dict]:
        """Lists OpenStack VMs and extracts their allocated IPs and status."""
        try:
            servers = list(self.conn.compute.servers())
        except Exception:
            return []

        sandboxes = []
        for s in servers:
            # Extract first IPv4 address
            ip = None
            addresses = getattr(s, "addresses", {}) or {}
            for net_name, addr_objs in addresses.items():
                for a in addr_objs:
                    if a.get("version") == 4:
                        ip = a.get("addr")
                        break
                if ip:
                    break

            team_name = s.name[4:] if s.name.startswith("sbx-") else s.name
            sandboxes.append({
                "server_id": s.id,
                "server_name": s.name,
                "team_name": team_name,
                "ip_address": ip,
                "status": s.status,
                "created_at": getattr(s, "created_at", None)
            })
        return sandboxes

