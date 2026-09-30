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
    time.sleep(1.5)

    # 4. Trigger reclamation engine
    reclaimed = ipam.reclaim_expired_leases()
    assert len(reclaimed) == 1
    assert reclaimed[0]["allocated_to"] == "team-alpha"

    # 5. IP should be freed and reusable for another team
    res_beta = ipam.allocate_ip("team-beta", lease_seconds=3600)
    assert res_beta["ip_address"] == "192.168.100.10"


def test_ipam_api_endpoints():
    """Tests /ipam/status and /ipam/allocations."""
    response = client.get("/ipam/status")
    assert response.status_code == 200
    data = response.json()
    assert "total_ips" in data
    assert "available_ips" in data


# ============================================================================
# Feature 2: Dual HA Gateways, Hot-Reload & 2-Phase Auto-Rollback
# ============================================================================

def test_gateway_hot_reload_and_case_a_rollback(tmp_path):
    """Tests syntax validation, hot-reload, and Case A Auto-Rollback on corrupt syntax."""
    cfg_dir = str(tmp_path / "gw_cfg")
    gw = GatewayService(config_dir=cfg_dir)

    # 1. Add valid route -> Hot-reload succeeds
    res = gw.add_route("team5", "10.0.0.50", port=8080)
    assert res["success"] is True
    assert res["action"] == "reloaded"
    assert gw.routes["team5"]["ip_address"] == "10.0.0.50"

    # 2. Case A: Invalid syntax (corrupt IP) -> Pre-reload rejection rollback
    res_bad = gw.add_route("team5", "not-a-valid-ip", port=80)
    assert res_bad["success"] is False
    assert res_bad["action"] == "auto_rollback_case_a"
    assert "Invalid IP" in res_bad["error"]

    # 3. Verify previous valid route preserved
    assert gw.routes["team5"]["ip_address"] == "10.0.0.50"


def test_gateway_case_b_runtime_recovery_rollback(tmp_path):
    """Tests Case B: Runtime reload failure actively restoring from .bak file."""
    cfg_dir = str(tmp_path / "gw_cfg_case_b")
    gw = GatewayService(config_dir=cfg_dir)

    # 1. Baseline route
    gw.add_route("team5", "10.0.0.50", port=80)

    # 2. Simulate runtime reload crash -> Case B rollback
    res_crash = gw.add_route("team6", "10.0.0.60", port=80, simulate_runtime_failure=True)
    assert res_crash["success"] is False
    assert res_crash["action"] == "auto_rollback_case_b"
    assert "restored to backup" in res_crash["error"].lower()

    # 3. Production routes preserved
    assert "team5" in gw.routes
    assert "team6" not in gw.routes


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
        "provision_vm": False  # Unit test mode
    }

    # Provision
    res = client.post("/sandboxes", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["team_name"] == "team7"
    assert "private_ip" in data
    assert "cstam.felcloud.tn" in data["fqdn"]

    # Get Sandbox
    res_get = client.get("/sandboxes/team7")
    assert res_get.status_code == 200
    assert res_get.json()["team_name"] == "team7"

    # Teardown
    res_del = client.delete("/sandboxes/team7")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "terminated"
