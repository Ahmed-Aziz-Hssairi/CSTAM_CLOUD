from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from app.services.gateway_service import GatewayService

router = APIRouter(
    prefix="/gateways",
    tags=["HA Gateways"]
)

gateway_service = GatewayService()


class RouteRequest(BaseModel):
    team_name: str
    ip_address: str
    port: int = Field(default=80, ge=1, le=65535)


class FailoverRequest(BaseModel):
    target_node: Optional[str] = Field(default=None, description="'primary' or 'standby'")


@router.get("/status")
def get_gateway_status():
    """
    Returns HA Gateway status (Primary vs Standby health, active VIP node, routing table).
    """
    return gateway_service.get_status()


@router.post("/routes")
def add_route(request: RouteRequest):
    """
    Adds/updates a routing rule with Zero-Downtime Hot-Reload and Auto-Rollback.
    """
    result = gateway_service.add_route(
        team_name=request.team_name,
        ip_address=request.ip_address,
        port=request.port
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.delete("/routes/{team_name}")
def remove_route(team_name: str):
    """
    Removes a routing rule with Zero-Downtime Hot-Reload.
    """
    return gateway_service.remove_route(team_name)


@router.post("/failover")
def trigger_failover(request: FailoverRequest):
    """
    Simulates / Triggers VRRP / Keepalived gateway failover between Primary and Standby.
    """
    return gateway_service.simulate_failover(target_node=request.target_node)
