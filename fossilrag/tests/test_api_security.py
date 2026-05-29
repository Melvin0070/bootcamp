"""Optional API-key auth: off by default, enforced when configured."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from fossilrag.api.security import require_api_key
from fossilrag.config import Settings


def _app(api_key: str | None) -> FastAPI:
    app = FastAPI(dependencies=[Depends(require_api_key)])
    app.state.settings = Settings(api_key=api_key)

    @app.get("/excavate")
    def excavate() -> dict:
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app


def test_auth_disabled_by_default_allows_all():
    client = TestClient(_app(None))
    assert client.get("/excavate").status_code == 200


def test_auth_enforced_when_key_set():
    client = TestClient(_app("s3cret"))
    assert client.get("/excavate").status_code == 401
    assert client.get("/excavate", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/excavate", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_healthz_stays_open_even_with_key():
    client = TestClient(_app("s3cret"))
    assert client.get("/healthz").status_code == 200
