"""
End-to-end pipeline test WITHOUT a network or a real model.

We inject a fake LLM (satisfying the two methods the pipeline uses) and a fake
faithfulness judge. This exercises the real orchestration — draft -> map+repair
-> constraint report -> faithfulness -> confidence -> ArticleJSON — and proves
the deterministic machinery is correct independent of Gemini.

This is dependency injection at the test boundary, NOT a mock client in the app:
the application code path still calls real Gemini in production.

Requires the runtime deps (pydantic, etc). Run:  python -m pytest tests/test_pipeline_offline.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed in this environment")

from app.schemas import (  # noqa: E402
    ArticleDraft, CompanyBrief, DraftBlock, Fact, ImageAsset,
)
from app.constraints_core import BlockType  # noqa: E402
from app.generate.pipeline import run_generation  # noqa: E402
from app.templates_def.templates import ONE_PAGER_V1  # noqa: E402

LONG_BODY = " ".join(["word"] * 140)          # way over the 95-word body cap
GOOD_HEADLINE = "Cutting idle fleet hours with AI"  # 6 words (within 4-9)


class FakeLLM:
    """Implements only what the pipeline calls. Not used in production."""

    configured = True

    def generate_structured(self, *, model, contents, schema, system_instruction=None, temperature=0.4):
        # return a draft: a good headline, an over-long body, plus the other blocks
        return ArticleDraft(blocks=[
            DraftBlock(id="headline", type=BlockType.HEADING, text=GOOD_HEADLINE,
                       citations=["recv.fact.1"]),
            DraftBlock(id="subhead", type=BlockType.SUBHEADING,
                       text="A practical look at how automation removes idle time for logistics fleets today",
                       citations=["recv.fact.1"]),
            DraftBlock(id="hero", type=BlockType.CAPTION, text="Fleet moving at dawn"),
            DraftBlock(id="body", type=BlockType.BODY, text=LONG_BODY, citations=["send.fact.1"]),
            DraftBlock(id="cta", type=BlockType.CTA, text="Book a fifteen minute routing demo today"),
        ])

    def generate_text(self, *, model, contents, system_instruction=None, temperature=0.4, max_output_tokens=None):
        # the faithfulness judge calls generate_text with the fact-checker system prompt and
        # expects a JSON verdict back; anything else is the "repair" step.
        if system_instruction and "fact-checker" in system_instruction:
            return '{"supported": true, "reason": "supported by grounding"}'
        # "repair": return a body comfortably within the 55-95 word window
        return " ".join(["word"] * 70)


def _brief(role, prefix):
    return CompanyBrief(
        role=role, name=f"{role.title} Co", industry="logistics",
        offerings=["routing engine"], value_props=["less idle time"],
        pain_points=["manual dispatch"], tone_signals=["practical"],
        key_stats=["30% less idle time"],
        facts=[Fact(id=f"{prefix}.fact.1", text="supporting fact", kind="fact", source_page=1)],
        logo_asset=f"{prefix}_logo" if role == "receiver" else None,
        images=[
            ImageAsset(id=f"{prefix}_logo", kind="logo", path=f"{prefix}_logo", width=120, height=120),
            ImageAsset(id=f"{prefix}_hero", kind="photo", path=f"{prefix}_hero", width=1200, height=800),
        ],
    )


def test_pipeline_repairs_and_reports():
    sender = _brief("sender", "send")
    receiver = _brief("receiver", "recv")

    result = run_generation(
        pair_id="acme__globex", sender=sender, receiver=receiver,
        prompt="show how the sender cuts the receiver's idle fleet time",
        template=ONE_PAGER_V1, request_id="testreq001",
        llm=FakeLLM(), judge=lambda text, grounding: True,  # judge: everything supported
    )

    # the over-long body must have been brought within limits (repaired, not truncated)
    body = next(b for b in result.blocks if b.id == "body")
    assert body.words <= 95, f"body still over limit: {body.words}"
    assert "body" in result.constraints.repaired_blocks
    assert result.constraints_ok, f"violations: {result.constraints.violations}"

    # the photo-role hero slot gets a photo (sender's, sender-first), not a logo
    hero = next(b for b in result.blocks if b.id == "hero")
    assert hero.image_ref == "send_hero"

    # required theme color coerced onto the headline
    headline = next(b for b in result.blocks if b.id == "headline")
    assert headline.color == "0FB5A6"

    # faithfulness + confidence populated and sane
    assert result.faithfulness.score == 1.0
    assert 0.0 <= result.confidence <= 1.0 and result.confidence > 0.5

    # reproducibility metadata stamped
    assert result.meta.request_id == "testreq001"
    assert result.meta.template_id == "one_pager_v1"


def test_pipeline_flags_dangling_citation():
    sender = _brief("sender", "send")
    receiver = _brief("receiver", "recv")

    class DanglingLLM(FakeLLM):
        def generate_structured(self, **kw):
            d = super().generate_structured(**kw)
            d.blocks[3].citations = ["does.not.exist"]  # body cites a non-existent fact
            return d

    result = run_generation(
        pair_id="acme__globex2", sender=sender, receiver=receiver,
        prompt="x", template=ONE_PAGER_V1, request_id="testreq002",
        llm=DanglingLLM(), judge=lambda text, grounding: True,
    )
    # judge says supported, but the deterministic dangling-citation check must catch it
    assert result.faithfulness.score < 1.0
    assert any("dangling" in u for u in result.faithfulness.unsupported_claims)


def test_faithfulness_judge_fails_closed():
    """A broken judge must ERROR the generation out (LLMError), never silently pass a claim
    or inflate the faithfulness score. Uses the real _gemini_judge path (no injected judge)."""
    from app.llm.gemini import LLMError

    class BadJudgeLLM(FakeLLM):
        def generate_text(self, *, model=None, contents=None, system_instruction=None, **kw):
            if system_instruction and "fact-checker" in system_instruction:
                return "not json at all"            # judge verdict unparseable -> must fail closed
            return " ".join(["word"] * 70)          # repair step still fine

    with pytest.raises(LLMError):
        run_generation(
            pair_id="acme__globex3", sender=_brief("sender", "send"),
            receiver=_brief("receiver", "recv"), prompt="x",
            template=ONE_PAGER_V1, request_id="testreq003",
            llm=BadJudgeLLM(),                       # no injected judge -> exercises _gemini_judge
        )


if __name__ == "__main__":
    test_pipeline_repairs_and_reports()
    test_pipeline_flags_dangling_citation()
    test_faithfulness_judge_fails_closed()
    print("offline pipeline tests passed.")
