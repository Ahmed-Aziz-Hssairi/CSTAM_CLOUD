import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dns import router as dns_router
from app.api.sandboxes import router as sandboxes_router
from app.api.gateways import router as gateways_router
from app.api.ipam import router as ipam_router
from app.services.sandbox_service import SandboxService

logger = logging.getLogger("FelCloudService")

sandbox_service = SandboxService()


async def background_lease_reclamation_worker():
    """
    Continuous Background Worker:
    Runs every 10 seconds to automatically detect expired sandbox leases,
    reclaim IP addresses immediately, and tear down expired OpenStack resources.
    """
    logger.info("Starting Background IPAM Lease Reclamation Engine...")
    while True:
        try:
            result = sandbox_service.reclaim_expired()
            if result.get("reclaimed_count", 0) > 0:
                logger.info(f"[IPAM AUTO-RECLAMATION] Purged {result['reclaimed_count']} expired sandbox(es): {result['reclaimed_sandboxes']}")
        except Exception as e:
            logger.error(f"[IPAM AUTO-RECLAMATION ERROR] {e}")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background task
    task = asyncio.create_task(background_lease_reclamation_worker())
    yield
    # Shutdown: Cancel task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="FelCloud Resilient IP Optimizer & Sandbox Orchestrator",
    description="CSTAM-FELCLOUD MVP - Automated Provisioning, Dual HA Gateways, IPAM Engine & Zero-Downtime Reload with Auto-Rollback",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for dashboard / web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register sub-routers
app.include_router(sandboxes_router)
app.include_router(gateways_router)
app.include_router(ipam_router)
app.include_router(dns_router)


@app.get("/", tags=["System"])
def root():
    return {
        "service": "FelCloud Resilient IP Optimizer",
        "phase": "Phase 1: Core Functionalities MVP",
        "status": "operational",
        "features": [
            "Automated OpenStack Sandbox Provisioning (Nova/Neutron)",
            "Dual Redundant HA Gateways (Keepalived/HAProxy/VRRP)",
            "Automated IPAM & Short Lease Reclamation Engine (Active Background Worker)",
            "Zero-Downtime Reload & Sub-second Auto-Rollback"
        ]
    }
