"""
Integration tests for SV-PRO FastAPI backend.

Uses FastAPI TestClient (httpx-based) — no real server or database required.
DB calls are mocked via dependency_overrides.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Thiết lập JWT_SECRET và ADMIN_PASSWORD trước khi import backend.main
# (backend/routers/auth.py đọc các biến này tại module-level)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-unit-tests")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Create TestClient with DB dependencies mocked."""
    # Patch DB init so app starts without real PostgreSQL
    with patch("backend.database.init_db", new_callable=AsyncMock):
        from backend.main import app
        with TestClient(app) as c:
            yield c


# ─── Health ───────────────────────────────────────────────────────────────────

class TestHealthEndpoint:

    def test_health_returns_200(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client: TestClient):
        response = client.get("/health")
        data = response.json()
        # Should have at minimum a status field
        assert isinstance(data, dict)
        assert any(k in data for k in ("status", "message", "ok"))


# ─── Auth ─────────────────────────────────────────────────────────────────────

class TestAuthEndpoints:

    def test_login_endpoint_exists(self, client: TestClient):
        """POST /api/auth/login should return 4xx (not 404) without valid creds."""
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code != 404, "Auth endpoint not found"

    def test_login_wrong_credentials_not_200(self, client: TestClient):
        response = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "badpass"},
        )
        assert response.status_code in (400, 401, 422, 500)


# ─── Cameras ─────────────────────────────────────────────────────────────────

class TestCamerasEndpoints:

    def test_get_cameras_without_auth_returns_401(self, client: TestClient):
        """GET /api/cameras without Authorization header → 401 or 403."""
        response = client.get("/api/cameras")
        assert response.status_code in (401, 403, 422)

    def test_cameras_endpoint_exists(self, client: TestClient):
        """Endpoint should exist (not 404 or 405)."""
        response = client.get("/api/cameras")
        assert response.status_code != 404

    def test_create_camera_without_auth_rejected(self, client: TestClient):
        """POST without auth → 401/403."""
        response = client.post("/api/cameras", json={
            "name": "Test Cam",
            "rtsp_url": "rtsp://192.168.1.1/stream",
        })
        assert response.status_code in (401, 403, 422)


# ─── Users ───────────────────────────────────────────────────────────────────

class TestUsersEndpoints:

    def test_get_users_endpoint_exists(self, client: TestClient):
        response = client.get("/api/users")
        assert response.status_code != 404

    def test_get_users_without_auth_rejected(self, client: TestClient):
        response = client.get("/api/users")
        assert response.status_code in (401, 403, 422)


# ─── Vehicles ────────────────────────────────────────────────────────────────

class TestVehiclesEndpoints:

    def test_get_vehicles_endpoint_exists(self, client: TestClient):
        response = client.get("/api/vehicles")
        assert response.status_code != 404

    def test_get_vehicles_without_auth_rejected(self, client: TestClient):
        response = client.get("/api/vehicles")
        assert response.status_code in (401, 403, 422)


# ─── Events ──────────────────────────────────────────────────────────────────

class TestEventsEndpoints:

    def test_get_events_endpoint_exists(self, client: TestClient):
        response = client.get("/api/events")
        assert response.status_code != 404

    def test_get_events_without_auth_rejected(self, client: TestClient):
        response = client.get("/api/events")
        assert response.status_code in (401, 403, 422)


# ─── Strangers ───────────────────────────────────────────────────────────────

class TestStrangersEndpoints:

    def test_get_strangers_endpoint_exists(self, client: TestClient):
        response = client.get("/api/strangers")
        assert response.status_code != 404

    def test_get_strangers_without_auth_rejected(self, client: TestClient):
        response = client.get("/api/strangers")
        assert response.status_code in (401, 403, 422)


# ─── Doors ───────────────────────────────────────────────────────────────────

class TestDoorsEndpoints:

    def test_get_doors_endpoint_exists(self, client: TestClient):
        response = client.get("/api/doors")
        assert response.status_code != 404

    def test_trigger_door_without_auth_rejected(self, client: TestClient):
        """POST /api/doors/door_01/trigger without auth → rejected."""
        response = client.post("/api/doors/door_01/trigger")
        assert response.status_code in (401, 403, 404, 422)


# ─── OpenAPI Docs ─────────────────────────────────────────────────────────────

class TestOpenAPIDocs:

    def test_swagger_docs_accessible(self, client: TestClient):
        """Swagger UI at /docs should be accessible."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_accessible(self, client: TestClient):
        response = client.get("/redoc")
        assert response.status_code == 200

    def test_openapi_schema_exists(self, client: TestClient):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        assert "info" in schema
        # Verify all main routers are in schema
        paths = schema["paths"]
        assert any("/cameras" in p for p in paths)
        assert any("/users" in p for p in paths)
        assert any("/vehicles" in p for p in paths)
        assert any("/events" in p for p in paths)
        assert any("/strangers" in p for p in paths)
