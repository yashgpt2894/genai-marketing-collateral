"""
Deterministic layout-constraint engine.

This is the heart of the "don't trust the LLM to count" story, and it is kept
*deliberately framework-free* (pure standard library, plain dataclasses). The
LLM never decides whether a block fits — this module does, against ground truth.

Because it has no pydantic/FastAPI/Gemini dependency, it is fully unit-testable
on its own (see tests/test_constraints.py) and runs anywhere.

Word counting note: we count whitespace-delimited tokens after stripping a small
set of punctuation, which matches how an editor counts words for a column far
better than naive ``len(text.split())`` (it does not count a trailing em-dash or
standalone "—" as a word). This is intentionally simple and predictable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ----------------------------------------------------------------------------- 
# Domain types (plain dataclasses — no framework dependency)
# -----------------------------------------------------------------------------


class BlockType(str, Enum):
    HEADING = "heading"
    SUBHEADING = "subheading"
    BODY = "body"
    CAPTION = "caption"
    CTA = "cta"


@dataclass(frozen=True)
class BlockSpec:
    """A slot in the layout template. The hard contract the article must satisfy."""
    id: str
    type: BlockType
    min_words: int
    max_words: int
    image_placeholder: bool = False
    theme_color: Optional[str] = None  # hex without '#', must be in the palette


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    name: str
    blocks: tuple[BlockSpec, ...]
    palette: tuple[str, ...]  # allowed hex colors (no '#')

    def block(self, block_id: str) -> Optional[BlockSpec]:
        return next((b for b in self.blocks if b.id == block_id), None)


@dataclass
class FilledBlock:
    """A block as produced by the writer (and mutated by the repair loop)."""
    id: str
    type: BlockType
    text: str
    citations: list[str] = field(default_factory=list)
    image_ref: Optional[str] = None
    color: Optional[str] = None


# ----------------------------------------------------------------------------- 
# Word counting + violations
# -----------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9''\-]*")


def count_words(text: str) -> int:
    """Count 'real' words: alphanumeric tokens, ignoring lone dashes/punctuation."""
    return len(_WORD_RE.findall(text or ""))


class ViolationKind(str, Enum):
    TOO_LONG = "too_long"
    TOO_SHORT = "too_short"
    MISSING_IMAGE = "missing_image"
    BAD_COLOR = "bad_color"
    EMPTY = "empty"
    UNKNOWN_BLOCK = "unknown_block"
    MISSING_BLOCK = "missing_block"


@dataclass
class Violation:
    block_id: str
    kind: ViolationKind
    detail: str
    # repair hint fields (used to build a targeted instruction)
    words: Optional[int] = None
    min_words: Optional[int] = None
    max_words: Optional[int] = None

    @property
    def is_length(self) -> bool:
        return self.kind in (ViolationKind.TOO_LONG, ViolationKind.TOO_SHORT)


def validate_block(block: FilledBlock, spec: BlockSpec, palette: tuple[str, ...]) -> list[Violation]:
    """Return every constraint this block violates (may be more than one)."""
    out: list[Violation] = []
    w = count_words(block.text)

    if not (block.text or "").strip():
        out.append(Violation(block.id, ViolationKind.EMPTY, "block text is empty"))
        return out  # nothing else is meaningful on an empty block

    if w > spec.max_words:
        out.append(Violation(
            block.id, ViolationKind.TOO_LONG,
            f"{w} words > max {spec.max_words}",
            words=w, min_words=spec.min_words, max_words=spec.max_words))
    elif w < spec.min_words:
        out.append(Violation(
            block.id, ViolationKind.TOO_SHORT,
            f"{w} words < min {spec.min_words}",
            words=w, min_words=spec.min_words, max_words=spec.max_words))

    if spec.image_placeholder and not block.image_ref:
        out.append(Violation(block.id, ViolationKind.MISSING_IMAGE,
                             "block requires an image asset but none is set"))

    if block.color and palette and block.color.upper() not in {c.upper() for c in palette}:
        out.append(Violation(block.id, ViolationKind.BAD_COLOR,
                             f"color {block.color} not in theme palette"))

    return out


def validate_document(blocks: list[FilledBlock], template: TemplateSpec) -> list[Violation]:
    """Validate the whole article against the template: per-block + structural."""
    out: list[Violation] = []
    by_id = {b.id: b for b in blocks}
    spec_ids = {b.id for b in template.blocks}

    # required blocks present?
    for spec in template.blocks:
        if spec.id not in by_id:
            out.append(Violation(spec.id, ViolationKind.MISSING_BLOCK,
                                 f"template requires block '{spec.id}'"))

    # unknown blocks the template doesn't define?
    for b in blocks:
        if b.id not in spec_ids:
            out.append(Violation(b.id, ViolationKind.UNKNOWN_BLOCK,
                                 f"block '{b.id}' is not in template '{template.id}'"))
            continue
        spec = template.block(b.id)
        assert spec is not None
        out.extend(validate_block(b, spec, template.palette))

    return out


def constraints_ok(blocks: list[FilledBlock], template: TemplateSpec) -> bool:
    return len(validate_document(blocks, template)) == 0


# ----------------------------------------------------------------------------- 
# Repair instructions (deterministic) + graceful fallback
# -----------------------------------------------------------------------------


def repair_instruction(block: FilledBlock, v: Violation) -> str:
    """
    Build a *targeted* instruction for one offending block. Only length
    violations are repaired by the model; structural ones are handled in code.
    """
    if v.kind is ViolationKind.TOO_LONG:
        target = max(v.min_words or 1, (v.max_words or 1) - 1)
        return (
            f"Shorten the following {block.type.value} to at most {v.max_words} words "
            f"(aim for about {target}). Preserve the key meaning, the tone, and any "
            f"factual claims with their citations. Do not add new facts. Return only the rewritten text.\n\n"
            f"TEXT:\n{block.text}"
        )
    if v.kind is ViolationKind.TOO_SHORT:
        target = min(v.max_words or 9999, (v.min_words or 0) + 2)
        return (
            f"Expand the following {block.type.value} to at least {v.min_words} words "
            f"(aim for about {target}) WITHOUT inventing new facts — only elaborate on "
            f"information already supported by the provided briefs, so the column does not "
            f"have awkward whitespace. Keep the tone. Return only the rewritten text.\n\n"
            f"TEXT:\n{block.text}"
        )
    raise ValueError(f"{v.kind} is not a model-repairable violation")


_SENT_END = re.compile(r"(?<=[.!?])\s+")


def truncate_to_words(text: str, max_words: int) -> str:
    """
    Graceful fallback when the model can't hit the limit after K tries:
    cut at the last sentence boundary that fits; if no boundary fits, hard-cut
    at the word budget and add an ellipsis. Never returns something over budget.
    """
    if count_words(text) <= max_words:
        return text

    sentences = _SENT_END.split(text.strip())
    kept: list[str] = []
    running = 0
    for s in sentences:
        w = count_words(s)
        if running + w <= max_words:
            kept.append(s)
            running += w
        else:
            break

    if kept:
        return " ".join(kept).strip()

    # no whole sentence fits — hard cut at the word budget
    words = _WORD_RE.findall(text)
    return " ".join(words[:max_words]).rstrip(",.;:") + "\u2026"
