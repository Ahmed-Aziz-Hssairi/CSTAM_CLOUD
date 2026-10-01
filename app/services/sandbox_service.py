import os
import logging
from typing import Dict, List, Optional
from app.services.ipam_service import IPAMService
from app.services.dns_service import DNSService
from app.services.compute_service import ComputeService
from app.services.gateway_service import GatewayService

logger = logging.getLogger("SandboxService")


class SandboxService:
    """
    Unified Sandbox Orchestrator for Phase 1 MVP.
    Coordinates:
    1. IPAM dynamic private IP allocation with short lease.
    2. OpenStack Nova VM creation on demo_network (attached to private IP).
    3. OpenStack Designate DNS registration:
       - Subdomain (e.g. team5.cstam.felcloud.tn) -> GATEWAY PUBLIC FLOATING IP.
    4. HA Dual Gateway Edge Routing Hot-Reload with Auto-Rollback:
       - HAProxy inspects Host header and reverse-proxies to VM private IP (10.0.0.10:80).
    """

    def __init__(
        self,
        ipam_service: Optional[IPAMService] = None,
        dns_service: Optional[DNSService] = None,
        compute_service: Optional[ComputeService] = None,
        gateway_service: Optional[GatewayService] = None
    ):
        self.ipam = ipam_service or IPAMService()
        self.dns = dns_service or DNSService()
        self.compute = compute_service or ComputeService()
        self.gateway = gateway_service or GatewayService()
        
        # Public Floating IP of the HA Edge Gateways (reachable from Internet)
        self.gateway_public_ip = os.getenv("GATEWAY_FLOATING_IP", os.getenv("GATEWAY_PUBLIC_IP", "10.0.0.5"))

        # Timeout configurations (in seconds)
        self.idle_timeout = int(os.getenv("IPAM_IDLE_TIMEOUT_SECONDS", "1800"))       # 30 min idle traffic
        self.shutoff_timeout = int(os.getenv("IPAM_SHUTOFF_TIMEOUT_SECONDS", "600"))  # 10 min prolonged shutoff


    def create_sandbox(
        self,
        team_name: str,
        lease_seconds: Optional[int] = None,
        image_name: Optional[str] = None,
        flavor_name: Optional[str] = None,
        port: int = 80,
        provision_vm: bool = True
    ) -> Dict:
        """
        Full 4-Step Orchestrated Provisioning:
        Step 1: Allocate private dynamic IP from IPAM with short lease.
        Step 2: Provision OpenStack VM with dedicated private IP.
        Step 3: Register DNS record pointing to GATEWAY FLOATING IP.
        Step 4: Update Edge HA Gateway routes (Host header -> private IP) with zero-downtime hot reload.
        """
        team_name = team_name.strip().lower()

        # 1. IPAM Private IP Allocation
        ip_info = self.ipam.allocate_ip(team_name, lease_seconds=lease_seconds)
        private_ip = ip_info["ip_address"]

        # 2. OpenStack VM Provisioning
        compute_result = None
        if provision_vm:
            try:
                compute_result = self.compute.create_sandbox_vm(
                    team_name=team_name,
                    ip_address=private_ip,
                    image_name=image_name,
                    flavor_name=flavor_name
                )
            except Exception as e:
                logger.warning(f"OpenStack VM provision warning for {team_name}: {e}")
                compute_result = {"status": "warning", "detail": str(e)}

        # 3. DNS Registration in Designate (Points to Gateway Public Floating IP for external access)
        dns_result = None
        try:
            dns_target_ip = self.gateway_public_ip
            dns_result = self.dns.create_record(
                hostname=team_name,
                ip_address=dns_target_ip,
                ttl=60
            )
        except Exception as e:
            logger.warning(f"OpenStack DNS register warning for {team_name}: {e}")
            dns_result = {"status": "warning", "detail": str(e)}

        # 4. Gateway Route Hot-Reload (Subdomain -> Private Sandbox IP)
        gw_result = self.gateway.add_route(
            team_name=team_name,
            ip_address=private_ip,
            port=port
        )

        fqdn = self.dns.normalize_hostname(team_name)

        return {
            "status": "ready",
            "team_name": team_name,
            "private_ip": private_ip,
            "gateway_public_ip": self.gateway_public_ip,
            "fqdn": fqdn,
            "lease": {
                "duration_seconds": ip_info.get("lease_duration_seconds"),
                "expires_at": ip_info.get("expires_at"),
                "status": ip_info.get("status")
            },
            "openstack_compute": compute_result,
            "dns_details": dns_result,
            "gateway_reload": gw_result
        }

    def delete_sandbox(self, team_name: str) -> Dict:
        """
        Full 4-Step Orchestrated Teardown:
        Step 1: Teardown OpenStack VM.
        Step 2: Remove Edge HA Gateway routing rule with hot-reload.
        Step 3: Delete DNS record from OpenStack Designate.
        Step 4: Immediate IPAM IP release for recycling.
        """
        team_name = team_name.strip().lower()

        # 1. Teardown Compute
        compute_result = None
        try:
            compute_result = self.compute.delete_sandbox_vm(team_name)
        except Exception as e:
            compute_result = {"status": "warning", "detail": str(e)}

        # 2. Gateway Route Teardown
        gw_result = self.gateway.remove_route(team_name)

        # 3. DNS Record Deletion
        dns_result = None
        try:
            dns_result = self.dns.delete_record(team_name)
        except Exception as e:
            dns_result = {"status": "warning", "detail": str(e)}

        # 4. Immediate IPAM IP Reclamation
        released_ip = self.ipam.release_ip(team_name)

        return {
            "status": "terminated",
            "team_name": team_name,
            "released_ip": released_ip,
            "compute_cleanup": compute_result,
            "gateway_cleanup": gw_result,
            "dns_cleanup": dns_result
        }

    def record_traffic(self, team_name: str) -> bool:
        """Records network/HTTP traffic activity for a team to reset idle timer."""
        return self.ipam.record_activity(team_name)

    def reclaim_expired(self) -> Dict:
        """
        Multi-Factor Auto-Reclamation Engine:
        1. Scans OpenStack Nova instances: flags SHUTOFF instances to start grace period.
        2. Reclaims expired leases, idle sandboxes (> idle_timeout), and abandoned shutoff VMs (> shutoff_timeout).
        3. Cascades teardown across Gateway (HAProxy), Designate DNS, and Nova/Neutron.
        """
        # Step 1: Sync OpenStack VM state (Detect SHUTOFF vs ACTIVE)
        try:
            live_vms = self.compute.list_sandbox_vms(include_all=True)
            for vm in live_vms:
                team = vm.get("team_name")
                status = str(vm.get("status", "")).upper()
                if team:
                    if status == "SHUTOFF":
                        self.ipam.update_shutoff_state(team, is_shutoff=True)
                    elif status == "ACTIVE":
                        self.ipam.update_shutoff_state(team, is_shutoff=False)
        except Exception as e:
            logger.debug(f"OpenStack shutoff state poll error: {e}")

        # Step 2: Multi-Factor Reclamation
        idle_t = getattr(self, "idle_timeout", int(os.getenv("IPAM_IDLE_TIMEOUT_SECONDS", "1800")))
        shutoff_t = getattr(self, "shutoff_timeout", int(os.getenv("IPAM_SHUTOFF_TIMEOUT_SECONDS", "600")))
        expired_allocations = self.ipam.reclaim_expired_leases(
            idle_timeout_seconds=idle_t,
            shutoff_timeout_seconds=shutoff_t
        )
        reclaimed_teams = []

        for item in expired_allocations:
            team_name = item["allocated_to"]
            reason = item.get("reclamation_reason", "lease_expired")
            if team_name:
                self.gateway.remove_route(team_name)
                try:
                    self.dns.delete_record(team_name)
                except Exception:
                    pass
                try:
                    self.compute.delete_sandbox_vm(team_name)
                except Exception:
                    pass
                reclaimed_teams.append({
                    "team_name": team_name,
                    "ip_address": item["ip_address"],
                    "reason": reason,
                    "expires_at": item.get("expires_at"),
                    "last_activity_at": item.get("last_activity_at"),
                    "shutoff_since": item.get("shutoff_since")
                })

        return {
            "reclaimed_count": len(reclaimed_teams),
            "reclaimed_sandboxes": reclaimed_teams
        }

    def get_sandbox(self, team_name: str) -> Optional[Dict]:
        """Returns comprehensive status of a sandbox."""
        team_name = team_name.strip().lower()
        ip_info = self.ipam.get_team_ip(team_name)
        if not ip_info:
            return None

        fqdn = self.dns.normalize_hostname(team_name)
        return {
            "team_name": team_name,
            "private_ip": ip_info["ip_address"],
            "gateway_public_ip": self.gateway_public_ip,
            "fqdn": fqdn,
            "lease_info": {
                "allocated_at": ip_info.get("allocated_at"),
                "lease_duration_seconds": ip_info.get("lease_duration_seconds"),
                "expires_at": ip_info.get("expires_at")
            },
            "gateway_route": self.gateway.routes.get(team_name),
            "status": "active"
        }

    def list_sandboxes(self) -> List[Dict]:
        """
        Lists all sandboxes, merging live OpenStack Nova instances with IPAM allocation leases.
        """
        allocations_map = {item["team_name"]: item for item in self.ipam.list_allocations()}
        
        # 1. Fetch live OpenStack instances
        live_vms = self.compute.list_sandbox_vms(include_all=True)
        
        results = []
        seen_teams = set()

        for vm in live_vms:
            team_name = vm["team_name"]
            seen_teams.add(team_name)
            ipam_info = allocations_map.get(team_name, {})
            
            ip_addr = vm["ip_address"] or ipam_info.get("ip_address")
            
            results.append({
                "team_name": team_name,
                "server_name": vm["server_name"],
                "server_id": vm["server_id"],
                "status": vm["status"],
                "private_ip": ip_addr,
                "gateway_public_ip": self.gateway_public_ip,
                "fqdn": self.dns.normalize_hostname(team_name),
                "allocated_at": ipam_info.get("allocated_at"),
                "expires_at": ipam_info.get("expires_at"),
                "source": "openstack_live"
            })

        # 2. Add any IPAM leases that might not yet have a running VM
        for team_name, item in allocations_map.items():
            if team_name not in seen_teams:
                results.append({
                    "team_name": team_name,
                    "server_name": f"sbx-{team_name}",
                    "server_id": None,
                    "status": "IPAM_LEASE_ONLY",
                    "private_ip": item["ip_address"],
                    "gateway_public_ip": self.gateway_public_ip,
                    "fqdn": self.dns.normalize_hostname(team_name),
                    "allocated_at": item.get("allocated_at"),
                    "expires_at": item.get("expires_at"),
                    "source": "ipam_db"
                })

        return results

    def power_action(self, team_name: str, action: str) -> Dict:
        """Controls power state (start / stop / reboot) of a sandbox instance."""
        team_name = team_name.strip().lower()
        action = action.strip().lower()
        if action in ["start", "on", "activate"]:
            return self.compute.start_sandbox_vm(team_name)
        elif action in ["stop", "off", "shutoff", "shutdown"]:
            return self.compute.stop_sandbox_vm(team_name)
        elif action in ["reboot", "restart"]:
            return self.compute.reboot_sandbox_vm(team_name)
        else:
            raise ValueError(f"Invalid power action '{action}'. Valid actions: start, stop, reboot.")


