"""Custom templates: model validation, tenant-scoped store round-trip, and the
POST /templates -> GET /templates -> /generate flow (no network, no real model)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")

from pydantic import ValidationError  # noqa: E402
from app.schemas import TemplateModel  # noqa: E402


def _good() -> dict:
    return {
        "id": "promo_card_v1", "name": "Promo card",
        "palette": ["0FB5A6", "10151E", "FFFFFF"],
        "blocks": [
            {"id": "headline", "type": "heading", "min_words": 3, "max_words": 8, "theme_color": "0FB5A6"},
            {"id": "body", "type": "body", "min_words": 20, "max_words": 95},
            {"id": "cta", "type": "cta", "min_words": 3, "max_words": 12},
        ],
    }


# ── validation ───────────────────────────────────────────────────────────────
def test_valid_template_passes():
    t = TemplateModel.model_validate(_good())
    assert t.to_spec().id == "promo_card_v1"   # maps onto the framework-free core


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d.update(blocks=[]),                               "no blocks"),
    (lambda d: d["blocks"].append(dict(d["blocks"][0])),         "duplicate block id"),
    (lambda d: d["blocks"][0].update(min_words=10, max_words=4), "max < min"),
    (lambda d: d.update(palette=["nothex"]),                     "bad palette hex"),
    (lambda d: d["blocks"][0].update(theme_color="123456"),      "theme_color not in palette"),
    (lambda d: d.update(id="Bad ID!"),                           "bad id pattern"),
    (lambda d: d.update(name=""),                                "empty name"),
])
def test_invalid_templates_rejected(mutate, why):
    d = _good()
    mutate(d)
    with pytest.raises(ValidationError):
        TemplateModel.model_validate(d)


# ── tenant-scoped store round-trip ────────────────────────────────────────────
def test_localstore_template_roundtrip(tmp_path):
    from app.store.local import LocalStore
    s = LocalStore(root=tmp_path)
    assert s.list_templates("t1") == [] and s.load_template("t1", "promo_card_v1") is None
    s.save_template("t1", TemplateModel.model_validate(_good()))
    got = s.load_template("t1", "promo_card_v1")
    assert got is not None and got.id == "promo_card_v1"
    assert [t.id for t in s.list_templates("t1")] == ["promo_card_v1"]
    assert s.list_templates("other_tenant") == []   # isolation


# ── endpoint flow ─────────────────────────────────────────────────────────────
def _client(tmp_path):
    pytest.importorskip("httpx", reason="TestClient needs httpx")
    import app.main as main
    from app.store.local import LocalStore
    from fastapi.testclient import TestClient
    from test_pipeline_offline import FakeLLM, _brief
    main.store = LocalStore(root=tmp_path)
    main.llm = FakeLLM()
    main.store.save_brief("default", "p1", _brief("sender", "send"))
    main.store.save_brief("default", "p1", _brief("receiver", "recv"))
    return TestClient(main.app)


def test_create_list_and_generate_with_custom_template(tmp_path):
    client = _client(tmp_path)
    tpl = _good()

    assert client.post("/templates", json=tpl).status_code == 201            # create
    ids = [t["id"] for t in client.get("/templates").json()]
    assert "promo_card_v1" in ids and "one_pager_v1" in ids                  # custom + built-in
    assert client.post("/templates", json=tpl).status_code == 409            # duplicate
    assert client.post("/templates", json={**tpl, "id": "one_pager_v1"}).status_code == 409  # built-in id
    assert client.post("/templates", json={**tpl, "id": "bad id!"}).status_code == 422        # invalid body

    g = client.post("/generate", json={"pair_id": "p1", "prompt": "show value",
                                       "template_id": "promo_card_v1"})
    assert g.status_code == 202, g.text
    res = client.get(f"/generate/p1/{g.json()['job_id']}").json()
    assert res["status"] == "done", res
    assert res["result"]["template_id"] == "promo_card_v1"                   # custom template resolved + used


def test_localstore_delete_roundtrips(tmp_path):
    from app.store.local import LocalStore
    s = LocalStore(root=tmp_path)
    s.save_template("t1", TemplateModel.model_validate(_good()))
    assert s.delete_template("t1", "promo_card_v1") is True
    assert s.load_template("t1", "promo_card_v1") is None
    assert s.delete_template("t1", "promo_card_v1") is False                  # already gone
    s.save_asset("t1", "p1", "logo", "png", b"\x89PNG\r\n")
    assert s.load_asset("t1", "p1", "logo") is not None
    assert s.delete_asset("t1", "p1", "logo") is True
    assert s.load_asset("t1", "p1", "logo") is None
    assert s.delete_asset("t1", "p1", "logo") is False                        # already gone


def test_localstore_clear_role_wipes_only_that_role(tmp_path):
    from app.store.local import LocalStore
    from app.schemas import CompanyBrief, Fact
    s = LocalStore(root=tmp_path)
    for role, pfx in (("sender", "send"), ("receiver", "recv")):
        s.save_upload("t", "p", role, "doc.pdf", b"%PDF-1.4 hi")
        s.save_asset("t", "p", f"{role}0_h_img_1_0", "png", b"\x89PNG")
        s.save_brief("t", "p", CompanyBrief(role=role, name=f"{role} co",
                                            facts=[Fact(id=f"{pfx}.fact.1", text="x")]))
    s.clear_role("t", "p", "sender")
    assert s.load_brief("t", "p", "sender") is None                  # brief gone
    assert s.load_asset("t", "p", "sender0_h_img_1_0") is None       # assets gone
    assert s.list_uploads("t", "p", "sender") == []                  # uploads gone
    # the other role is untouched
    assert s.load_brief("t", "p", "receiver") is not None
    assert s.load_asset("t", "p", "receiver0_h_img_1_0") is not None
    assert s.list_uploads("t", "p", "receiver") != []


def test_edit_and_delete_template_endpoints(tmp_path):
    client = _client(tmp_path)
    tpl = _good()
    assert client.post("/templates", json=tpl).status_code == 201
    edited = {**tpl, "name": "Promo card v2", "blocks": [dict(b) for b in tpl["blocks"]]}
    edited["blocks"][1]["max_words"] = 120
    r = client.put("/templates/promo_card_v1", json=edited)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Promo card v2"
    got = [t for t in client.get("/templates").json() if t["id"] == "promo_card_v1"][0]
    assert got["name"] == "Promo card v2"                                     # edit persisted
    assert client.put("/templates/one_pager_v1", json={**tpl, "id": "one_pager_v1"}).status_code == 409   # built-in
    assert client.put("/templates/promo_card_v1", json={**tpl, "id": "other"}).status_code == 400          # id mismatch
    assert client.put("/templates/missing_v1", json={**tpl, "id": "missing_v1"}).status_code == 404         # not found
    assert client.delete("/templates/one_pager_v1").status_code == 409        # built-in protected
    assert client.delete("/templates/promo_card_v1").status_code == 200       # deleted
    assert "promo_card_v1" not in [t["id"] for t in client.get("/templates").json()]
    assert client.delete("/templates/promo_card_v1").status_code == 404       # already gone


def test_delete_asset_endpoint(tmp_path):
    import app.main as main
    client = _client(tmp_path)
    main.store.save_asset("default", "p1", "logo", "png", b"\x89PNG\r\n")
    assert client.get("/assets/p1/logo").status_code == 200
    assert client.delete("/assets/p1/logo").status_code == 200
    assert client.get("/assets/p1/logo").status_code == 404                   # gone
    assert client.delete("/assets/p1/logo").status_code == 404                # already gone
