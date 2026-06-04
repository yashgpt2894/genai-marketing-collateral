"""Cache: key determinism, brief-sensitive invalidation, fail-open parsing.
No Redis needed — we test the pure key logic, NullCache, and load_cached."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed in this environment")

from app.cache import NullCache, brief_fingerprint, gen_cache_key, load_cached  # noqa: E402
from app.constraints_core import BlockType  # noqa: E402
from app.schemas import (  # noqa: E402
    ArticleBlock, ArticleJSON, CompanyBrief, ConstraintReport,
    FaithfulnessReport, Fact, GenerationMeta,
)


def _brief(role, prefix, extra=""):
    return CompanyBrief(role=role, name=f"{role} co",
                        facts=[Fact(id=f"{prefix}.fact.1", text="a fact" + extra)])


def _article(pair="p1"):
    return ArticleJSON(
        pair_id=pair, template_id="one_pager_v1",
        blocks=[ArticleBlock(id="headline", type=BlockType.HEADING, text="hello", words=1)],
        assets={}, confidence=0.9, constraints_ok=True,
        constraints=ConstraintReport(ok=True, blocks_within_limits=1, total_blocks=1),
        faithfulness=FaithfulnessReport(score=1.0, supported=1, total_claims=1),
        meta=GenerationMeta(request_id="r1", model_writer="gemini-2.5-pro",
                            prompt_version="p1/t1", template_id="one_pager_v1"),
    )


class _FakeCache:
    def __init__(self, val=None):
        self.val = val
    def get(self, key):
        return self.val
    def set(self, key, value, ttl_seconds):
        self.val = value


def test_nullcache_is_always_a_miss():
    c = NullCache()
    c.set("k", "v", 60)            # must be a silent no-op
    assert c.get("k") is None
    assert c.enabled is False


def test_key_deterministic_and_prompt_sensitive():
    k1 = gen_cache_key("t", "pair", "prompt A", "one_pager_v1", "fp1")
    k2 = gen_cache_key("t", "pair", "prompt A", "one_pager_v1", "fp1")
    k3 = gen_cache_key("t", "pair", "prompt B", "one_pager_v1", "fp1")
    assert k1 == k2 and k1 != k3
    assert k1.startswith("gen:t:pair:")


def test_fingerprint_invalidates_when_briefs_change():
    s, r = _brief("sender", "send"), _brief("receiver", "recv")
    fp = brief_fingerprint(s, r)
    assert brief_fingerprint(s, r) == fp                              # stable
    assert brief_fingerprint(s, _brief("receiver", "recv", "!")) != fp  # changed brief -> new key


def test_load_cached_roundtrip_miss_and_garbage():
    art = _article()
    assert load_cached(_FakeCache(None), "k") is None                # miss
    assert load_cached(_FakeCache("{not json"), "k") is None         # garbage -> fail-open
    got = load_cached(_FakeCache(art.model_dump_json()), "k")        # round-trip
    assert got is not None and got.pair_id == "p1"
