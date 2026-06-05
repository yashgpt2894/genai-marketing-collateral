"""Eval harness: offline run over cases exercises the real pipeline and aggregates;
the committed golden set passes its own thresholds offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed in this environment")

from app.eval.harness import EvalCase, load_cases, run_eval  # noqa: E402
from app.schemas import CompanyBrief, Fact, ImageAsset  # noqa: E402

_GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "evals", "golden_cases.json")


def _brief(role, prefix):
    return CompanyBrief(
        role=role, name=f"{role.title()} Co", industry="logistics",
        offerings=["routing"], value_props=["less idle time"], pain_points=["manual dispatch"],
        tone_signals=["practical"], key_stats=["30% less idle time"],
        facts=[Fact(id=f"{prefix}.fact.1", text="a supporting fact", source_page=1)],
        logo_asset=f"{prefix}_logo" if role == "receiver" else None,
        images=[
            ImageAsset(id=f"{prefix}_logo", kind="logo", path=f"{prefix}_logo", width=120, height=120),
            ImageAsset(id=f"{prefix}_hero", kind="photo", path=f"{prefix}_hero", width=1200, height=800),
        ],
    )


def test_run_eval_offline_all_pass_both_templates():
    cases = [
        EvalCase(id="c1_one_pager", prompt="show how the sender helps the receiver",
                 sender=_brief("sender", "send"), receiver=_brief("receiver", "recv")),
        EvalCase(id="c2_two_column", template_id="two_column_v1", prompt="make the case",
                 sender=_brief("sender", "send"), receiver=_brief("receiver", "recv")),
    ]
    rep = run_eval(cases, live=False, min_faithfulness=0.9, min_pass_rate=1.0)
    assert rep.ok
    assert rep.pass_rate == 1.0
    assert len(rep.results) == 2
    assert all(r.constraints_ok for r in rep.results)
    assert rep.mean_faithfulness >= 0.9
    assert rep.total_cost_usd == 0.0          # offline stand-in records no token usage


def test_committed_golden_set_passes_offline():
    cases, th = load_cases(_GOLDEN)
    assert len(cases) >= 2
    rep = run_eval(cases, live=False,
                   min_faithfulness=th["min_faithfulness"], min_pass_rate=th["min_pass_rate"])
    assert rep.ok, [(r.id, r.constraints_ok, r.faithfulness) for r in rep.results]
