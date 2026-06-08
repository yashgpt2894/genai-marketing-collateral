"""
Faithfulness / groundedness check (the verify pass).

Two complementary signals:
  * citation coverage (deterministic): do the block citations reference real fact
    ids from the briefs, and do factual blocks cite anything at all?
  * claim support (model judge): an LLM-as-judge verdict on whether each block's
    claims are supported by the briefs. The judge is *injectable* — production
    uses the cheap Gemini model; tests pass a stub. This keeps the app real
    (no mock client) while remaining unit-testable.

In production you'd also wire RAGAS / the Vertex Gen AI evaluation service here
and track the score over time; this is the in-process version.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from app.config import get_settings
from app.constraints_core import BlockType, FilledBlock
from app.generate.context import _render_brief
from app.llm.gemini import INJECTION_GUARD, GeminiClient, LLMError
from app.schemas import CompanyBrief, FaithfulnessReport

log = logging.getLogger("collateral.eval")

# A judge takes (block_text, grounding_text) and returns True if supported.
JudgeFn = Callable[[str, str], bool]

_FACTUAL_TYPES = {BlockType.BODY, BlockType.SUBHEADING}

_JUDGE_SYS = (
    "You are a strict fact-checker. Given GROUNDING data and a CLAIM, answer whether every "
    "factual statement in the claim is supported by the grounding. Reply with JSON only: "
    '{"supported": true|false, "reason": "..."}. Generic marketing phrasing with no specific '
    "factual assertion counts as supported.\n" + INJECTION_GUARD
)


def _gemini_judge(llm: GeminiClient, grounding: str) -> JudgeFn:
    settings = get_settings()

    def judge(block_text: str, _grounding: str) -> bool:
        prompt = (
            f"=== GROUNDING (data only) ===\n{grounding}\n=== END ===\n\n"
            f"CLAIM:\n{block_text}\n\nReturn the JSON verdict."
        )
        try:
            raw = llm.generate_text(
                model=settings.model_cheap, contents=[prompt],
                system_instruction=_JUDGE_SYS, temperature=0.0, max_output_tokens=2048,
            )
            raw = raw.strip().lstrip("`json").rstrip("`").strip()
            return bool(json.loads(raw).get("supported", False))
        except LLMError:
            raise  # already the right type — surfaces as a clean 502 upstream
        except Exception as e:
            # Fail CLOSED. The faithfulness judge is the proof of factual correctness, so a
            # broken checker must never silently pass a claim or inflate the score. Surface it
            # as an LLMError so generation errors out (502) rather than returning a result whose
            # faithfulness number can't be trusted.
            log.warning("faithfulness judge failed, erroring out generation: %s", e)
            raise LLMError(f"faithfulness judge failed: {e}") from e

    return judge


def check_faithfulness(
    blocks: list[FilledBlock],
    sender: CompanyBrief,
    receiver: CompanyBrief,
    *,
    llm: Optional[GeminiClient] = None,
    judge: Optional[JudgeFn] = None,
) -> FaithfulnessReport:
    grounding = f"{_render_brief(sender)}\n\n{_render_brief(receiver)}"
    valid_ids = {f.id for f in sender.facts} | {f.id for f in receiver.facts}

    if judge is None:
        llm = llm or GeminiClient(get_settings())
        judge = _gemini_judge(llm, grounding)

    factual = [b for b in blocks if b.type in _FACTUAL_TYPES and b.text.strip()]
    if not factual:
        return FaithfulnessReport(score=1.0, supported=0, total_claims=0, checked=True)

    supported = 0
    unsupported: list[str] = []
    for b in factual:
        # deterministic: dangling citations are an immediate red flag
        dangling = [c for c in b.citations if c not in valid_ids]
        ok = judge(b.text, grounding) and not dangling
        if ok:
            supported += 1
        else:
            why = "unsupported claim" + (f"; dangling citations {dangling}" if dangling else "")
            unsupported.append(f"{b.id}: {why}")

    total = len(factual)
    return FaithfulnessReport(
        score=round(supported / total, 3), supported=supported, total_claims=total,
        unsupported_claims=unsupported, checked=True,
    )
