"""
Token-usage accounting + per-model cost — verified offline with fake response
objects (no network). Proves usage accumulates per model and cost is priced per
model (Pro vs Flash-Lite), not blended.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")

from app.llm.gemini import GeminiClient  # noqa: E402
from app.pricing import band, cost_usd  # noqa: E402


class _UM:
    def __init__(self, p, c, t):
        self.prompt_token_count, self.candidates_token_count, self.total_token_count = p, c, t


class _Resp:
    def __init__(self, um):
        self.usage_metadata = um


def test_usage_accumulates_per_model():
    c = GeminiClient()
    c.reset_usage()
    c._record_usage("gemini-3.5-pro", _Resp(_UM(100, 50, 150)))
    c._record_usage("gemini-3.5-pro", _Resp(_UM(20, 10, 30)))
    c._record_usage("gemini-3.5-flash-lite", _Resp(_UM(40, 5, 45)))
    u = c.usage()
    assert u["gemini-3.5-pro"] == {"input": 120, "output": 60, "total": 180, "calls": 2}
    assert u["gemini-3.5-flash-lite"]["input"] == 40


def test_reset_clears():
    c = GeminiClient()
    c.reset_usage()
    c._record_usage("m", _Resp(_UM(1, 1, 2)))
    c.reset_usage()
    assert c.usage() == {}


def test_missing_usage_metadata_is_safe():
    c = GeminiClient()
    c.reset_usage()
    c._record_usage("m", object())  # no usage_metadata attr
    assert c.usage() == {}


def test_band():
    assert band("gemini-3.5-pro") == "pro"
    assert band("gemini-3.5-flash") == "flash"
    assert band("gemini-3.5-flash-lite") == "flash-lite"


def test_cost_priced_per_model():
    usage = {
        "gemini-3.5-pro": {"input": 1_000_000, "output": 1_000_000},        # 1.25 + 10.00
        "gemini-3.5-flash-lite": {"input": 1_000_000, "output": 0},          # 0.10
    }
    assert cost_usd(usage) == 11.35


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("usage/cost tests passed.")
