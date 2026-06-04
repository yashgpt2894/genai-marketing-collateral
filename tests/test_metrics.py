"""Metrics: the pure row builder matches the BQ schema; emit is off-by-default
and fail-open (never raises, returns False when disabled)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed in this environment")

from app.constraints_core import BlockType  # noqa: E402
from app.metrics import TABLE_SCHEMA, build_row, emit_generation_metric  # noqa: E402
from app.schemas import (  # noqa: E402
    ArticleBlock, ArticleJSON, ConstraintReport, FaithfulnessReport, GenerationMeta,
)


def _article(pair="p1"):
    return ArticleJSON(
        pair_id=pair, template_id="one_pager_v1",
        blocks=[ArticleBlock(id="headline", type=BlockType.HEADING, text="hello", words=1)],
        assets={}, confidence=0.94, constraints_ok=True,
        constraints=ConstraintReport(ok=True, blocks_within_limits=5, total_blocks=5),
        faithfulness=FaithfulnessReport(score=1.0, supported=2, total_claims=2),
        meta=GenerationMeta(request_id="r1", model_writer="gemini-2.5-pro",
                            prompt_version="p1/t1", template_id="one_pager_v1",
                            total_tokens=11057, cost_usd=0.0119),
    )


def test_build_row_matches_schema_and_values():
    row = build_row("acme", "p1", _article(), latency_ms=1234, cached=True, source="generate")
    assert set(row.keys()) == {name for name, _ in TABLE_SCHEMA}      # row == table contract
    assert row["tenant"] == "acme"
    assert row["cached"] is True
    assert row["latency_ms"] == 1234
    assert row["confidence"] == 0.94
    assert row["faithfulness"] == 1.0
    assert row["blocks_within_limits"] == 5
    assert row["cost_usd"] == 0.0119
    assert row["model_writer"] == "gemini-2.5-pro"


def test_emit_is_off_by_default_and_failopen():
    # METRICS_BACKEND defaults to "none" -> no-op, returns False, never raises
    assert emit_generation_metric("acme", "p1", _article()) is False
