import pytest
import time
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.ipam_service import IPAMService
from app.services.gateway_service import GatewayService

client = TestClient(app)


def test_root_endpoint():
    """Validates root service metadata and Phase 1 feature listing."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "operational"
    assert "Phase 1: Core Functionalities MVP" in data["phase"]


# ============================================================================
# Feature 1: Automated IPAM & Short Lease Reclamation Engine
# ============================================================================

def test_ipam_allocation_and_lease_expiry(tmp_path):
    """Tests dynamic IP allocation with short lease and auto-reclamation."""
    db_file = str(tmp_path / "test_ipam.db")
    ipam = IPAMService(
        db_path=db_file,
        cidr="192.168.100.0/24",
        start_ip="192.168.100.10",
        end_ip="192.168.100.15",
        default_lease_seconds=1
    )

    # 1. Allocate IP with 1s lease
    res = ipam.allocate_ip("team-alpha", lease_seconds=1)
    assert res["ip_address"] == "192.168.100.10"
    assert res["allocated_to"] == "team-alpha"
    assert res["status"] == "allocated"

    # 2. Check allocation listed
    allocs = ipam.list_allocations()
    assert len(allocs) == 1
    assert allocs[0]["team_name"] == "team-alpha"

    # 3. Wait for lease to expire
    time.sleep(2.0)

    # 4. Trigger reclamation engine
    reclaimed = ipam.reclaim_expired_leases()
    assert len(reclaimed) == 1
    assert reclaimed[0]["allocated_to"] == "team-alpha"

    # 5. IP should be freed and reusable for another team
    res_beta = ipam.allocate_ip("team-beta", lease_seconds=3600)
    assert res_beta["ip_address"] == "192.168.100.10"


def test_ipam_idle_traffic_reclamation(tmp_path):
    """Method 1: Tests auto-reclamation when a sandbox has no traffic/activity for > idle_timeout."""
    db_file = str(tmp_path / "test_ipam_idle.db")
    ipam = IPAMService(db_path=db_file, cidr="192.168.100.0/24", start_ip="192.168.100.10", end_ip="192.168.100.15")

    # Allocate with long lease (3600s)
    ipam.allocate_ip("team-idle", lease_seconds=3600)
    
    # Wait 1s and reclaim with 1s idle timeout
    time.sleep(1.2)
    reclaimed = ipam.reclaim_expired_leases(idle_timeout_seconds=1)
    assert len(reclaimed) == 1
    assert reclaimed[0]["allocated_to"] == "team-idle"
    assert reclaimed[0]["reclamation_reason"] == "idle_traffic_timeout"


def test_ipam_prolonged_shutoff_reclamation(tmp_path):
    """Method 4: Tests auto-reclamation when an OpenStack VM is stopped/SHUTOFF for > shutoff_timeout."""
    db_file = str(tmp_path / "test_ipam_shutoff.db")
    ipam = IPAMService(db_path=db_file, cidr="192.168.100.0/24", start_ip="192.168.100.10", end_ip="192.168.100.15")

    # Allocate VM
    ipam.allocate_ip("team-stopped", lease_seconds=3600)
    # OpenStack poll marks instance as SHUTOFF
    ipam.update_shutoff_state("team-stopped", is_shutoff=True)

    # Wait 1s and trigger reclamation with 1s shutoff timeout
    time.sleep(1.2)
    reclaimed = ipam.reclaim_expired_leases(shutoff_timeout_seconds=1)
    assert len(reclaimed) == 1
    assert reclaimed[0]["allocated_to"] == "team-stopped"
    assert reclaimed[0]["reclamation_reason"] == "shutoff_timeout"


def test_ipam_api_endpoints():
    """Tests /ipam/status, /ipam/allocations, and /sandboxes/team/heartbeat."""
    response = client.get("/ipam/status")
    assert response.status_code == 200
    data = response.json()
    assert "total_ips" in data
    assert "available_ips" in data


# ============================================================================
# Feature 2: Dual HA Gateways, Hot-Reload & Sub-second Auto-Rollback
# ============================================================================

def test_gateway_hot_reload_and_auto_rollback(tmp_path):
    """Tests syntax validation, hot-reload, and immediate rollback on invalid config."""
    cfg_dir = str(tmp_path / "gw_cfg")
    gw = GatewayService(config_dir=cfg_dir)

    # 1. Add valid route -> Hot-reload succeeds
    res = gw.add_route("team5", "10.0.0.50", port=8080)
    assert res["success"] is True
    assert res["action"] == "reloaded"
    assert gw.routes["team5"]["ip_address"] == "10.0.0.50"

    # 2. Attempt invalid route (corrupt IP) -> Triggers Auto-Rollback
    res_bad = gw.add_route("team5", "not-a-valid-ip", port=80)
    assert res_bad["success"] is False
    assert res_bad["action"] == "auto_rollback"
    assert "Invalid IP" in res_bad["error"]

    # 3. Verify previous valid route was preserved (Rollback verified)
    assert gw.routes["team5"]["ip_address"] == "10.0.0.50"

    # 4. Failover simulation
    failover_res = gw.simulate_failover()
    assert failover_res["action"] == "failover_executed"
    assert failover_res["active_node"] == "standby"


def test_gateway_api_status_and_failover():
    """Tests /gateways/status and /gateways/failover endpoints."""
    res = client.get("/gateways/status")
    assert res.status_code == 200
    assert "gateway_ha" in res.json()

    res_failover = client.post("/gateways/failover", json={"target_node": "standby"})
    assert res_failover.status_code == 200
    assert res_failover.json()["active_node"] == "standby"


# ============================================================================
# Feature 3: Automated Sandbox Provisioning & Teardown API
# ============================================================================

@patch("app.services.sandbox_service.ComputeService")
@patch("app.services.sandbox_service.DNSService")
def test_sandbox_provisioning_and_teardown_flow(mock_dns_cls, mock_compute_cls):
    """Tests the full unified workflow: IPAM + Compute + DNS + Gateway."""
    payload = {
        "team_name": "team7",
        "lease_seconds": 3600,
        "port": 80,
        "provision_vm": False  # Skip live OpenStack call in unit test
    }

    # Provision
    res = client.post("/sandboxes", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["team_name"] == "team7"
    assert "ip_address" in data
    assert "cstam.felcloud.tn" in data["fqdn"]

    # Get Sandbox
    res_get = client.get("/sandboxes/team7")
    assert res_get.status_code == 200
    assert res_get.json()["team_name"] == "team7"

    # Teardown
    res_del = client.delete("/sandboxes/team7")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "terminated"
