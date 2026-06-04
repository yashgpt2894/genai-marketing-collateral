"""
API integration test for the async /generate contract (no network, no real model).

We inject a fake LLM + a temp LocalStore into the app and drive it with FastAPI's
TestClient. TestClient runs the BackgroundTask during the POST, so by the time we
poll, the job is done — proving: POST returns 202 + job_id (never blocks), the
work runs off the request path, and the poll endpoint returns the full ArticleJSON.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")
pytest.importorskip("httpx", reason="TestClient needs httpx")

import app.main as main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.store.local import LocalStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_pipeline_offline import FakeLLM, _brief  # noqa: E402


def _client(tmp_path):
    main.store = LocalStore(root=tmp_path)   # endpoints read the module global at call time
    main.llm = FakeLLM()                     # configured=True; deterministic draft + repair
    main.store.save_brief("default", "p1", _brief("sender", "send"))
    main.store.save_brief("default", "p1", _brief("receiver", "recv"))
    return TestClient(main.app)


def test_generate_returns_202_then_pollable_result(tmp_path):
    client = _client(tmp_path)

    r = client.post("/generate", json={"pair_id": "p1", "prompt": "show value",
                                        "template_id": "one_pager_v1"})
    assert r.status_code == 202, r.text                  # accepted, not blocked
    job = r.json()
    assert job["status"] == "processing" and job["job_id"] and job["poll"]

    # background task already ran under TestClient -> poll returns the article
    p = client.get(f"/generate/p1/{job['job_id']}")
    assert p.status_code == 200
    body = p.json()
    assert body["status"] == "done", body
    res = body["result"]
    assert res["pair_id"] == "p1" and res["blocks"]
    assert "cost_usd" in res["meta"] and "total_tokens" in res["meta"]


def test_generate_missing_briefs_is_409(tmp_path):
    client = _client(tmp_path)
    r = client.post("/generate", json={"pair_id": "nope", "prompt": "make an article",
                                        "template_id": "one_pager_v1"})
    assert r.status_code == 409


def test_poll_unknown_job_is_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/generate/p1/doesnotexist").status_code == 404


# --- auth (#8) ---------------------------------------------------------------
def test_auth_disabled_allows_calls_without_token(tmp_path):
    # AUTH_MODE defaults to "none" -> no token needed; missing briefs is 409, not 401
    client = _client(tmp_path)
    r = client.post("/generate", json={"pair_id": "nobody", "prompt": "make an article",
                                       "template_id": "one_pager_v1"})
    assert r.status_code == 409


def test_auth_google_mode_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_mode", "google")  # turn auth on
    client = _client(tmp_path)
    r = client.post("/generate", json={"pair_id": "p1", "prompt": "make an article",
                                       "template_id": "one_pager_v1"})
    assert r.status_code == 401  # no bearer token


# --- hardening (#9) ----------------------------------------------------------
def test_upload_over_size_limit_is_413(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)  # any non-empty file is too big
    client = _client(tmp_path)
    files = {"files": ("big.pdf", b"%PDF-1.4 over the limit", "application/pdf")}
    r = client.post("/companies/p1/documents", data={"role": "sender"}, files=files)
    assert r.status_code == 413


def test_generate_idempotency_key_dedups(tmp_path):
    client = _client(tmp_path)
    hdr = {"Idempotency-Key": "abc-123"}
    body = {"pair_id": "p1", "prompt": "make an article", "template_id": "one_pager_v1"}
    j1 = client.post("/generate", json=body, headers=hdr).json()
    j2 = client.post("/generate", json=body, headers=hdr).json()
    assert j1["job_id"] == j2["job_id"]          # same key -> same job
    assert j2.get("idempotent") is True
