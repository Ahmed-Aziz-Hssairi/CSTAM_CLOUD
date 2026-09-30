from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from app.services.sandbox_service import SandboxService

router = APIRouter(
    prefix="/sandboxes",
    tags=["Sandboxes"]
)

sandbox_service = SandboxService()


class CreateSandboxRequest(BaseModel):
    team_name: str = Field(..., min_length=2, max_length=32, description="Team identifier (e.g. team5)")
    lease_seconds: Optional[int] = Field(default=3600, ge=60, le=86400, description="Lease duration in seconds")
    image_name: Optional[str] = Field(default=None, description="OpenStack image name")
    flavor_name: Optional[str] = Field(default=None, description="OpenStack flavor name")
    port: Optional[int] = Field(default=80, ge=1, le=65535, description="Target application port on sandbox")
    provision_vm: Optional[bool] = Field(default=True, description="Whether to trigger Nova VM creation or IPAM/DNS/Gateway only")


@router.post("", response_model=dict)
def provision_sandbox(request: CreateSandboxRequest):
    """
    Automated OpenStack Sandbox Provisioning [Phase 1].
    - Allocates dynamic IP with short lease.
    - Provisions OpenStack VM with dedicated port.
    - Registers DNS subdomain in OpenStack Designate.
    - Hot-reloads Dual HA Edge Gateway routing table.
    """
    try:
        return sandbox_service.create_sandbox(
            team_name=request.team_name,
            lease_seconds=request.lease_seconds,
            image_name=request.image_name,
            flavor_name=request.flavor_name,
            port=request.port,
            provision_vm=request.provision_vm
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[dict])
def list_sandboxes():
    """Lists all active sandboxes with assigned IPs and FQDNs."""
    return sandbox_service.list_sandboxes()


@router.get("/{team_name}", response_model=dict)
def get_sandbox(team_name: str):
    """Gets details and lease status for a specific sandbox."""
    res = sandbox_service.get_sandbox(team_name)
    if not res:
        raise HTTPException(status_code=404, detail=f"Sandbox for '{team_name}' not found.")
    return res


@router.delete("/{team_name}", response_model=dict)
def teardown_sandbox(team_name: str):
    """
    Automated Teardown & Resource Reclamation:
    - Teardown OpenStack VM.
    - Removes HA Gateway route.
    - Deletes DNS record.
    - Immediately releases IPAM IP.
    """
    try:
        return sandbox_service.delete_sandbox(team_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reclaim-expired", response_model=dict)
def trigger_reclamation():
    """Triggers the Reclamation Engine to purge expired sandbox leases."""
    return sandbox_service.reclaim_expired()
