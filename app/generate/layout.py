"""
Map the draft onto the template and enforce the hard constraints.

This is the automation of the editors' trial-and-error, and the place the
'LLMs can't count' problem is solved structurally:

    1. map draft blocks -> FilledBlock (engine type)
    2. validate against the template (app/constraints_core) — GROUND TRUTH
    3. for each LENGTH violation, ask the cheap model to rewrite ONLY that block
    4. re-validate; repeat up to max_repair_iters
    5. anything still too long after K tries -> truncate at a sentence boundary
       and flag for human review (graceful degradation, never loop forever)

Structural issues (missing/unknown block, missing image, bad color) are fixed in
code where possible (drop unknowns, attach the logo, coerce theme color) rather
than asking the model again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import get_settings
from app.constraints_core import (
    FilledBlock, TemplateSpec, ViolationKind,
    count_words, repair_instruction, truncate_to_words, validate_block, validate_document,
)
from app.llm.gemini import INJECTION_GUARD, GeminiClient
from app.schemas import ArticleDraft

log = logging.getLogger("collateral.layout")

_REPAIR_SYS = (
    "You are an editor adjusting a single block of marketing copy to fit a strict "
    "word budget. Preserve meaning, tone and any factual claims. Do not introduce new "
    "facts. Return ONLY the rewritten text, with no preamble or quotation marks.\n" + INJECTION_GUARD
)


@dataclass
class RepairResult:
    blocks: list[FilledBlock]
    repaired: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    iterations: int = 0


def _coerce_structural(blocks: list[FilledBlock], template: TemplateSpec,
                       image_by_block: dict[str, str]) -> list[FilledBlock]:
    spec_ids = {b.id for b in template.blocks}
    # drop blocks the template doesn't define
    kept = [b for b in blocks if b.id in spec_ids]
    by_id = {b.id: b for b in kept}
    for spec in template.blocks:
        b = by_id.get(spec.id)
        if b is None:
            continue
        # image choice is deterministic — the code owns it; override whatever the model emitted
        # (the model tends to echo a fact id here; never trust it for an asset reference)
        if spec.image_placeholder:
            b.image_ref = image_by_block.get(spec.id)
        # coerce a required theme color
        if spec.theme_color:
            b.color = spec.theme_color
    return kept


def map_and_repair(
    draft: ArticleDraft,
    template: TemplateSpec,
    *,
    llm: GeminiClient | None = None,
    image_by_block: dict[str, str] | None = None,
) -> RepairResult:
    settings = get_settings()
    llm = llm or GeminiClient(settings)

    blocks = [FilledBlock(id=b.id, type=b.type, text=b.text, citations=list(b.citations),
                          image_ref=b.image_ref, color=b.color) for b in draft.blocks]
    blocks = _coerce_structural(blocks, template, image_by_block or {})

    res = RepairResult(blocks=blocks)
    by_id = {b.id: b for b in blocks}

    for it in range(1, settings.max_repair_iters + 1):
        violations = [v for v in validate_document(blocks, template) if v.is_length]
        if not violations:
            break
        res.iterations = it
        for v in violations:
            blk = by_id.get(v.block_id)
            if blk is None:
                continue
            instruction = repair_instruction(blk, v)
            try:
                # block text is DATA inside the instruction; system rules guard it
                new_text = llm.generate_text(
                    model=settings.model_cheap, contents=[instruction],
                    system_instruction=_REPAIR_SYS, temperature=0.3,
                    max_output_tokens=2048,  # thinking models need headroom over the visible output
                )
                blk.text = new_text
                if blk.id not in res.repaired:
                    res.repaired.append(blk.id)
            except Exception as e:  # repair call failed — leave it for fallback
                log.warning("repair call failed for block %s: %s", blk.id, e)

    # graceful fallback: hard-cap any block still over max
    for spec in template.blocks:
        blk = by_id.get(spec.id)
        if blk and count_words(blk.text) > spec.max_words:
            blk.text = truncate_to_words(blk.text, spec.max_words)
            res.truncated.append(blk.id)
            log.info("block %s truncated to %d words (flagged for human review)", blk.id, spec.max_words)

    return res
