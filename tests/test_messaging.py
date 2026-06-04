"""
Async-parse dispatch (#4) — verified offline. We don't need a real Pub/Sub to
prove the wiring: test that a push envelope round-trips through decode_push, and
that POST /internal/parse decodes it and dispatches to the parse function.
(The publish path itself is exercised only against real Pub/Sub at deploy.)
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")
pytest.importorskip("httpx", reason="TestClient needs httpx")

from app.messaging import decode_push  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _envelope(payload: dict) -> dict:
    return {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()},
            "subscription": "projects/x/subscriptions/parse-sub"}


def test_decode_push_round_trip():
    payload = {"tenant": "t", "pair_id": "p", "job_id": "j", "role": "sender", "keys": ["sender_a.pdf"]}
    assert decode_push(_envelope(payload)) == payload


def test_decode_push_rejects_non_envelope():
    with pytest.raises(ValueError):
        decode_push({"not": "a pubsub push"})


def test_internal_parse_dispatches_to_worker(monkeypatch):
    import app.main as main
    calls = []
    monkeypatch.setattr(main, "_parse_job", lambda *a: calls.append(a))
    client = TestClient(main.app)

    payload = {"tenant": "default", "pair_id": "p1", "job_id": "j1", "role": "sender", "keys": ["sender_x.pdf"]}
    r = client.post("/internal/parse", json=_envelope(payload))
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert calls == [("default", "p1", "j1", "sender", ["sender_x.pdf"])]


def test_internal_parse_bad_envelope_is_400(monkeypatch):
    import app.main as main
    client = TestClient(main.app)
    assert client.post("/internal/parse", json={"nope": 1}).status_code == 400
