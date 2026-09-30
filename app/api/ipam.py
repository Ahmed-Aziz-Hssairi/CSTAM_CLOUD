from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from app.services.ipam_service import IPAMService

router = APIRouter(
    prefix="/ipam",
    tags=["IPAM"]
)

ipam_service = IPAMService()


class AllocateRequest(BaseModel):
    team_name: str
    lease_seconds: Optional[int] = Field(default=3600, ge=60, le=86400)


@router.get("/status")
def get_pool_status():
    """Returns IP pool statistics (total, allocated, available, range)."""
    return ipam_service.get_pool_status()


@router.get("/allocations")
def list_allocations():
    """Lists all active allocations with lease expiration timestamps."""
    return ipam_service.list_allocations()


@router.post("/allocate")
def allocate_ip(request: AllocateRequest):
    """Allocates an IP with a short lease."""
    try:
        return ipam_service.allocate_ip(
            team_name=request.team_name,
            lease_seconds=request.lease_seconds
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/release/{team_name}")
def release_ip(team_name: str):
    """Releases an IP back to the pool."""
    released = ipam_service.release_ip(team_name)
    if not released:
        raise HTTPException(status_code=404, detail=f"No IP allocation found for '{team_name}'.")
    return {"action": "released", "team_name": team_name, "ip_address": released}


@router.post("/reclaim-expired")
def reclaim_expired():
    """Scans and reclaims expired leases."""
    reclaimed = ipam_service.reclaim_expired_leases()
    return {"reclaimed_count": len(reclaimed), "reclaimed": reclaimed}
