"""
The generate pipeline as one typed function: the deterministic DAG.

    assemble context -> draft (grounded+cited) -> map+repair -> validate
    -> faithfulness -> confidence -> ArticleJSON

Confidence is a simple, explainable blend of constraint compliance and
faithfulness, penalised when blocks had to be truncated. It is deliberately NOT
the model's self-reported confidence (which is unreliable) — it is computed from
ground-truth checks, mirroring the Case 1 lesson about calibrated confidence.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.pricing import cost_usd
from app.constraints_core import TemplateSpec, count_words
from app.eval.constraint_checks import build_report
from app.eval.faithfulness import JudgeFn, check_faithfulness
from app.generate.context import ContextAssembler, default_assembler
from app.generate.layout import map_and_repair
from app.generate.writer import draft_article
from app.llm.gemini import GeminiClient
from app.schemas import (
    ArticleBlock, ArticleJSON, CompanyBrief, GenerationMeta,
)
from app.templates_def.templates import TEMPLATE_VERSION

log = logging.getLogger("collateral.pipeline")


def _confidence(constraints_ok: bool, within_ratio: float, faithfulness: float, truncated: int) -> float:
    base = 0.45 * within_ratio + 0.45 * faithfulness + (0.10 if constraints_ok else 0.0)
    base -= 0.05 * truncated  # each forced truncation is a small confidence hit
    return round(max(0.0, min(1.0, base)), 3)


def run_generation(
    pair_id: str,
    sender: CompanyBrief,
    receiver: CompanyBrief,
    prompt: str,
    template: TemplateSpec,
    request_id: str,
    *,
    llm: GeminiClient | None = None,
    assembler: ContextAssembler | None = None,
    judge: JudgeFn | None = None,
) -> ArticleJSON:
    settings = get_settings()
    llm = llm or GeminiClient(settings)
    if hasattr(llm, "reset_usage"):
        llm.reset_usage()  # fresh per-generation token tally
    assembler = assembler or default_assembler()
    logo_ref = receiver.logo_asset or sender.logo_asset

    # 1) draft (grounded + cited, controlled generation)
    draft = draft_article(sender, receiver, prompt, template,
                          llm=llm, assembler=assembler, logo_ref=logo_ref)

    # 2) map onto template + enforce limits in code (+ targeted model repairs)
    repair = map_and_repair(draft, template, llm=llm, logo_ref=logo_ref)

    # 3) reports
    constraints = build_report(repair.blocks, template, repair)
    faithfulness = check_faithfulness(repair.blocks, sender, receiver, llm=llm, judge=judge)

    # 4) confidence from ground-truth checks (not the model's self-estimate)
    within_ratio = (constraints.blocks_within_limits / constraints.total_blocks
                    if constraints.total_blocks else 0.0)
    confidence = _confidence(constraints.ok, within_ratio, faithfulness.score, len(repair.truncated))

    # 5) assemble the wire object
    spec_by_id = {b.id: b for b in template.blocks}
    out_blocks: list[ArticleBlock] = []
    assets: dict[str, str] = {}
    for b in repair.blocks:
        spec = spec_by_id.get(b.id)
        out_blocks.append(ArticleBlock(
            id=b.id, type=b.type, text=b.text, words=count_words(b.text),
            min_words=spec.min_words if spec else None,
            max_words=spec.max_words if spec else None,
            citations=b.citations, image_ref=b.image_ref, color=b.color,
        ))
        if b.image_ref:
            assets[b.id] = b.image_ref

    # token usage + estimated cost (per-model), summed across draft/repair/verify
    usage = llm.usage() if hasattr(llm, "usage") else {}
    in_tok = sum(v.get("input", 0) for v in usage.values())
    out_tok = sum(v.get("output", 0) for v in usage.values())
    tot_tok = sum(v.get("total", 0) for v in usage.values())

    return ArticleJSON(
        pair_id=pair_id, template_id=template.id, blocks=out_blocks, assets=assets,
        confidence=confidence, constraints_ok=constraints.ok,
        constraints=constraints, faithfulness=faithfulness,
        meta=GenerationMeta(
            request_id=request_id, model_writer=settings.model_writer,
            prompt_version=f"{settings.prompt_version}/{TEMPLATE_VERSION}",
            template_id=template.id, repair_iterations=repair.iterations,
            input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok,
            cost_usd=cost_usd(usage),
        ),
    )
