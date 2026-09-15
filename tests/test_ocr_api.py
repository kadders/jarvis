from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.main import app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_ocr_health(client: TestClient) -> None:
    r = client.get("/api/v1/ocr/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"
    assert j["ready"] is True
    assert j["detector"]["ready"] is True
    assert j["concurrency_limit"] >= 1
    assert "mode" in j["detector"]
    assert "artifact_size_bytes" in j["detector"]


def test_upload_recognize_with_objects(client: TestClient) -> None:
    img = Image.new("RGB", (48, 32), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    r = client.post(
        "/api/v1/images",
        files={"file": ("t.png", raw, "image/png")},
    )
    assert r.status_code == 201
    iid = r.json()["image_id"]
    r2 = client.post(
        "/api/v1/ocr/recognize",
        json={"image_id": iid, "prompt": "Transcribe visible text.", "mode": "fast"},
    )
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert "request" in data and "result" in data and "meta" in data
    assert "objects" in data["result"]
    assert isinstance(data["result"]["objects"], list)
    assert data["delivery"]["attempted"] is False


def test_recognize_invalid_arguments(client: TestClient) -> None:
    r = client.post(
        "/api/v1/ocr/recognize",
        json={"prompt": "x"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_arguments"


def test_delete_image_bad_uuid(client: TestClient) -> None:
    r = client.delete("/api/v1/images/not-a-uuid")
    assert r.status_code == 400
