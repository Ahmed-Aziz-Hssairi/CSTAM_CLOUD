# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, IPvAnyAddress, Field

from app.services.dns_service import DNSService


router = APIRouter(
    prefix="/dns",
    tags=["DNS"]
)

dns_service = DNSService()


class DNSRecordRequest(BaseModel):

    hostname: str = Field(
        min_length=1,
        max_length=63
    )

    ip_address: IPvAnyAddress

    ttl: int = Field(
        default=60,
        ge=30,
        le=86400
    )


@router.post("/records")
def create_dns_record(
    request: DNSRecordRequest
):

    try:

        return dns_service.create_record(
            hostname=request.hostname,
            ip_address=str(request.ip_address),
            ttl=request.ttl
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.put("/records/{hostname}")
def update_dns_record(
    hostname: str,
    request: DNSRecordRequest
):

    try:

        return dns_service.update_record(
            hostname=hostname,
            ip_address=str(request.ip_address),
            ttl=request.ttl
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.get("/records/{hostname}")
def get_dns_record(hostname: str):

    try:

        record = dns_service.get_record(
            hostname
        )

        if not record:

            raise HTTPException(
                status_code=404,
                detail="DNS record not found"
            )

        return {
            "hostname": record.name,
            "type": record.type,
            "records": record.records,
            "ttl": record.ttl
        }

    except HTTPException:

        raise

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.delete("/records/{hostname}")
def delete_dns_record(hostname: str):

    try:

        return dns_service.delete_record(
            hostname
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
