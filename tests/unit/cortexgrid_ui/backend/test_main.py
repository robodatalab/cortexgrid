from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid_ui.backend.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboards_links_mlflow_and_ray_to_their_configured_urls() -> None:
    """The rail's external links. The endpoint's whole job is to hand the
    frontend the URLs the cluster is actually configured with, so the URLs are
    stubbed here rather than read from the machine running the test - which is
    how this asserted a shape nobody serves for as long as it did."""
    with (
        patch(
            "cortexgrid_ui.backend.main.get_mlflow_tracking_uri",
            return_value="http://mlflow.test:5000",
        ),
        patch(
            "cortexgrid_ui.backend.main.get_ray_job_server_uri",
            return_value="http://ray.test:8265",
        ),
    ):
        response = client.get("/api/dashboards")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "mlflow", "url": "http://mlflow.test:5000"},
        {"id": "ray", "url": "http://ray.test:8265"},
    ]
