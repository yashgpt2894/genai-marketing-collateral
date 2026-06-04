"""Build the API-facing ConstraintReport from the deterministic engine output."""
from __future__ import annotations

from app.constraints_core import FilledBlock, TemplateSpec, count_words, validate_document
from app.generate.layout import RepairResult
from app.schemas import ConstraintReport


def build_report(blocks: list[FilledBlock], template: TemplateSpec, repair: RepairResult) -> ConstraintReport:
    violations = validate_document(blocks, template)
    within = 0
    for spec in template.blocks:
        b = next((x for x in blocks if x.id == spec.id), None)
        if b and spec.min_words <= count_words(b.text) <= spec.max_words:
            within += 1
    return ConstraintReport(
        ok=len(violations) == 0,
        blocks_within_limits=within,
        total_blocks=len(template.blocks),
        violations=[f"{v.block_id}: {v.detail}" for v in violations],
        repaired_blocks=repair.repaired,
        truncated_blocks=repair.truncated,
    )
