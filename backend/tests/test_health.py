from fastapi.testclient import TestClient

from app.main import app


def test_health_route_is_registered() -> None:
    operation = app.openapi()["paths"]["/api/v1/health"]
    assert "get" in operation


def test_health_reports_unavailable_when_services_are_down(monkeypatch) -> None:
    async def database_down():
        return "unavailable", None, {}

    async def redis_down():
        return "unavailable"

    monkeypatch.setattr("app.api.routes.health._database_status", database_down)
    monkeypatch.setattr("app.api.routes.health._redis_status", redis_down)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"
    assert body["redis"] == "unavailable"
    assert body["last_store_sync"] is None
    assert body["last_store_syncs"] == {}
