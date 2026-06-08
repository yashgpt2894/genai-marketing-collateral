"""Store-level asset deletion + clear_role, and the DELETE /assets endpoint
(no network, no real model). The custom-template-CRUD tests were removed with that feature."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")


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


def test_localstore_asset_delete_roundtrip(tmp_path):
    from app.store.local import LocalStore
    s = LocalStore(root=tmp_path)
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


def test_delete_asset_endpoint(tmp_path):
    import app.main as main
    client = _client(tmp_path)
    main.store.save_asset("default", "p1", "logo", "png", b"\x89PNG\r\n")
    assert client.get("/assets/p1/logo").status_code == 200
    assert client.delete("/assets/p1/logo").status_code == 200
    assert client.get("/assets/p1/logo").status_code == 404                   # gone
    assert client.delete("/assets/p1/logo").status_code == 404                # already gone
