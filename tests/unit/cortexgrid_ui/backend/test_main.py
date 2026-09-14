from fastapi.testclient import TestClient

from cortexflow_ui.backend.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboards_lists_mlflow_ray_minio() -> None:
    response = client.get("/api/dashboards")
    assert response.status_code == 200

    payload = response.json()
    ids = [d["id"] for d in payload]
    assert ids == ["mlflow", "ray", "minio"]

    by_id = {d["id"]: d for d in payload}
    assert by_id["mlflow"]["port"] == 5000
    assert by_id["ray"]["port"] == 8265
    assert by_id["minio"]["port"] == 9001

    for entry in payload:
        assert entry["name"]
        assert entry["description"]
        assert isinstance(entry["port"], int)
