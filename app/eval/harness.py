"""
Eval harness — a golden-set regression gate for the generation pipeline.

Two modes:
  * **offline (default)** — injects a deterministic stand-in LLM, so the run is
    free, fast, and hermetic. It exercises the real machinery (draft -> map+repair
    -> constraint report -> faithfulness -> confidence) and asserts the pipeline
    still produces in-limit, cited output. This is the bit you run in CI.
  * **--live** — uses the real Gemini path to measure *quality* (faithfulness,
    confidence, cost) on the golden prompts. Run before a model/prompt change.

It aggregates pass-rate / mean-faithfulness / mean-confidence / total-cost and
exits non-zero when below threshold — i.e. it behaves like a test, not a notebook.

    python -m app.eval.harness                  # offline smoke (CI)
    python -m app.eval.harness --live            # real Gemini quality run
    python -m app.eval.harness --cases evals/golden_cases.json --min-faithfulness 0.9
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.constraints_core import BlockType
from app.generate.pipeline import run_generation
from app.schemas import ArticleDraft, CompanyBrief, DraftBlock
from app.templates_def.templates import get_template

_DEFAULT_CASES = Path(__file__).resolve().parents[2] / "evals" / "golden_cases.json"


# -----------------------------------------------------------------------------
# offline stand-in: deterministic, template-aware, no network
# -----------------------------------------------------------------------------
class _OfflineLLM:
    """Deterministic LLM stand-in for hermetic runs. Builds an in-limit draft
    from the template; the real pipeline still does all the mapping/validation."""

    configured = True

    def __init__(self, template, sender: CompanyBrief, receiver: CompanyBrief):
        self._template = template
        self._fact = next((f.id for f in (list(receiver.facts) + list(sender.facts))), None)

    def generate_structured(self, *, model, contents, schema, system_instruction=None, temperature=0.4):
        blocks = []
        for b in self._template.blocks:
            n = b.min_words + 1  # comfortably inside [min_words, max_words]
            cites = [self._fact] if (self._fact and b.type == BlockType.BODY) else []
            blocks.append(DraftBlock(id=b.id, type=b.type, text=" ".join(["lorem"] * n), citations=cites))
        return ArticleDraft(blocks=blocks)

    def generate_text(self, *, model, contents, system_instruction=None, temperature=0.4, max_output_tokens=None):
        return " ".join(["lorem"] * 8)


# -----------------------------------------------------------------------------
# case + report types
# -----------------------------------------------------------------------------
@dataclass
class EvalCase:
    id: str
    prompt: str
    sender: CompanyBrief
    receiver: CompanyBrief
    template_id: str = "one_pager_v1"


@dataclass
class CaseResult:
    id: str
    confidence: float
    faithfulness: float
    constraints_ok: bool
    within: str
    cost_usd: float
    passed: bool


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    pass_rate: float = 0.0
    mean_faithfulness: float = 0.0
    mean_confidence: float = 0.0
    total_cost_usd: float = 0.0
    ok: bool = False


def load_cases(path: str | Path) -> tuple[list[EvalCase], dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [
        EvalCase(
            id=c["id"], prompt=c["prompt"], template_id=c.get("template_id", "one_pager_v1"),
            sender=CompanyBrief.model_validate(c["sender"]),
            receiver=CompanyBrief.model_validate(c["receiver"]),
        )
        for c in doc["cases"]
    ]
    thresholds = {
        "min_faithfulness": doc.get("min_faithfulness", 0.9),
        "min_pass_rate": doc.get("min_pass_rate", 1.0),
    }
    return cases, thresholds


def run_eval(
    cases: list[EvalCase], *, live: bool = False,
    min_faithfulness: float = 0.9, min_pass_rate: float = 1.0,
    judge: Optional[Callable[[str, str], bool]] = None,
) -> EvalReport:
    """Run every case through the real pipeline and aggregate. Pure-ish: no printing."""
    rep = EvalReport()
    # offline gets a True judge (the deterministic stand-in is faithful by construction);
    # live uses the real Gemini judge unless one is injected.
    eval_judge = judge if judge is not None else (None if live else (lambda text, grounding: True))

    for case in cases:
        template = get_template(case.template_id)
        llm = None if live else _OfflineLLM(template, case.sender, case.receiver)
        result = run_generation(
            case.id, case.sender, case.receiver, case.prompt, template,
            f"eval-{case.id}", llm=llm, judge=eval_judge,
        )
        passed = bool(result.constraints_ok and result.faithfulness.score >= min_faithfulness)
        rep.results.append(CaseResult(
            id=case.id, confidence=result.confidence, faithfulness=result.faithfulness.score,
            constraints_ok=result.constraints_ok,
            within=f"{result.constraints.blocks_within_limits}/{result.constraints.total_blocks}",
            cost_usd=result.meta.cost_usd, passed=passed,
        ))

    n = len(rep.results) or 1
    rep.pass_rate = sum(r.passed for r in rep.results) / n
    rep.mean_faithfulness = sum(r.faithfulness for r in rep.results) / n
    rep.mean_confidence = sum(r.confidence for r in rep.results) / n
    rep.total_cost_usd = sum(r.cost_usd for r in rep.results)
    rep.ok = rep.pass_rate >= min_pass_rate and rep.mean_faithfulness >= min_faithfulness
    return rep


def _print_report(rep: EvalReport, mode: str) -> None:
    print(f"\n  eval ({mode})  —  {len(rep.results)} cases")
    print("  " + "-" * 64)
    print(f"  {'case':28} {'conf':>5} {'faith':>6} {'within':>7} {'$':>9}  ok")
    for r in rep.results:
        print(f"  {r.id[:28]:28} {r.confidence:5.2f} {r.faithfulness:6.2f} "
              f"{r.within:>7} {r.cost_usd:9.4f}  {'✓' if r.passed else '✗'}")
    print("  " + "-" * 64)
    print(f"  pass-rate {rep.pass_rate:.0%} · mean-faith {rep.mean_faithfulness:.2f} · "
          f"mean-conf {rep.mean_confidence:.2f} · total ${rep.total_cost_usd:.4f}")
    print(f"  RESULT: {'PASS ✓' if rep.ok else 'FAIL ✗'}\n")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Golden-set eval for the generation pipeline.")
    ap.add_argument("--cases", default=str(_DEFAULT_CASES), help="path to golden_cases.json")
    ap.add_argument("--live", action="store_true", help="use real Gemini (costs money) instead of the offline stand-in")
    ap.add_argument("--min-faithfulness", type=float, default=None)
    ap.add_argument("--min-pass-rate", type=float, default=None)
    args = ap.parse_args(argv)

    cases, th = load_cases(args.cases)
    min_faith = args.min_faithfulness if args.min_faithfulness is not None else th["min_faithfulness"]
    min_pass = args.min_pass_rate if args.min_pass_rate is not None else th["min_pass_rate"]

    rep = run_eval(cases, live=args.live, min_faithfulness=min_faith, min_pass_rate=min_pass)
    _print_report(rep, "live" if args.live else "offline")
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
