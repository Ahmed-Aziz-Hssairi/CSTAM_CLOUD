import pytest
from unittest.mock import MagicMock, patch
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "operational"
    assert "FelCloud" in data["service"]


@patch("app.api.dns.dns_service")
def test_create_dns_record(mock_dns_service):
    mock_dns_service.create_record.return_value = {
        "action": "created",
        "hostname": "team1.cstam.felcloud.tn.",
        "ip_address": "10.0.0.10",
        "ttl": 60
    }

    payload = {
        "hostname": "team1",
        "ip_address": "10.0.0.10",
        "ttl": 60
    }

    response = client.post("/dns/records", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "created"
    assert data["hostname"] == "team1.cstam.felcloud.tn."
    assert data["ip_address"] == "10.0.0.10"
    mock_dns_service.create_record.assert_called_once_with(
        hostname="team1",
        ip_address="10.0.0.10",
        ttl=60
    )


@patch("app.api.dns.dns_service")
def test_get_dns_record_success(mock_dns_service):
    mock_record = MagicMock()
    mock_record.name = "team1.cstam.felcloud.tn."
    mock_record.type = "A"
    mock_record.records = ["10.0.0.10"]
    mock_record.ttl = 60
    mock_dns_service.get_record.return_value = mock_record

    response = client.get("/dns/records/team1")
    assert response.status_code == 200
    data = response.json()
    assert data["hostname"] == "team1.cstam.felcloud.tn."
    assert data["records"] == ["10.0.0.10"]


@patch("app.api.dns.dns_service")
def test_get_dns_record_not_found(mock_dns_service):
    mock_dns_service.get_record.return_value = None

    response = client.get("/dns/records/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "DNS record not found"


@patch("app.api.dns.dns_service")
def test_update_dns_record(mock_dns_service):
    mock_dns_service.update_record.return_value = {
        "action": "updated",
        "hostname": "team1.cstam.felcloud.tn.",
        "ip_address": "10.0.0.20",
        "ttl": 120
    }

    payload = {
        "hostname": "team1",
        "ip_address": "10.0.0.20",
        "ttl": 120
    }

    response = client.put("/dns/records/team1", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "updated"
    assert data["ip_address"] == "10.0.0.20"


@patch("app.api.dns.dns_service")
def test_delete_dns_record(mock_dns_service):
    mock_dns_service.delete_record.return_value = {
        "action": "deleted",
        "hostname": "team1.cstam.felcloud.tn."
    }

    response = client.delete("/dns/records/team1")
    assert response.status_code == 200
    assert response.json() == {
        "action": "deleted",
        "hostname": "team1.cstam.felcloud.tn."
    }


def test_validation_invalid_ip():
    payload = {
        "hostname": "team1",
        "ip_address": "invalid-ip",
        "ttl": 60
    }
    response = client.post("/dns/records", json=payload)
    assert response.status_code == 422
