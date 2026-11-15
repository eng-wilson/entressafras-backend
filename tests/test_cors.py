from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_preflight_allows_any_origin():
    response = client.options(
        "/api/rotations/recommendations",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_simple_request_has_allow_origin_header():
    response = client.get("/api/health", headers={"Origin": "https://example.org"})
    assert response.headers["access-control-allow-origin"] == "*"
