"""
Tests for the deterministic constraint engine.

These intentionally have NO third-party dependency (no pydantic / FastAPI /
Gemini), so they run anywhere — including offline CI — and prove that the
"enforce limits in code, not by trusting the model" logic is correct
independently of the LLM.

Run:  python -m pytest tests/test_constraints.py   (or: python tests/test_constraints.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constraints_core import (  # noqa: E402
    BlockSpec, BlockType, FilledBlock, TemplateSpec, ViolationKind,
    count_words, validate_block, validate_document, constraints_ok,
    repair_instruction, truncate_to_words,
)

PALETTE = ("0FB5A6", "10151E", "FFFFFF")

TEMPLATE = TemplateSpec(
    id="one_pager_v1", name="One-pager", palette=PALETTE,
    blocks=(
        BlockSpec("headline", BlockType.HEADING, min_words=3, max_words=8, theme_color="0FB5A6"),
        BlockSpec("body", BlockType.BODY, min_words=40, max_words=90),
        BlockSpec("hero", BlockType.CAPTION, min_words=2, max_words=12, image_placeholder=True),
    ),
)


def _ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_count_words_ignores_lone_dashes():
    _ok(count_words("Cutting idle fleet hours with AI") == 6, "basic count")
    _ok(count_words("hello \u2014 world") == 2, "em-dash is not a word")
    _ok(count_words("state-of-the-art real-time AI") == 3, "hyphenated counts once")
    _ok(count_words("") == 0 and count_words(None) == 0, "empty/None safe")


def test_too_long_flagged():
    b = FilledBlock("headline", BlockType.HEADING, "one two three four five six seven eight nine ten")
    spec = TEMPLATE.block("headline")
    vs = validate_block(b, spec, PALETTE)
    _ok(any(v.kind is ViolationKind.TOO_LONG for v in vs), "should flag too long")


def test_too_short_flagged():
    b = FilledBlock("body", BlockType.BODY, "far too short")
    spec = TEMPLATE.block("body")
    vs = validate_block(b, spec, PALETTE)
    _ok(any(v.kind is ViolationKind.TOO_SHORT for v in vs), "should flag too short")


def test_missing_image_and_bad_color():
    b = FilledBlock("hero", BlockType.CAPTION, "a short caption here", color="ABCDEF")
    spec = TEMPLATE.block("hero")
    vs = validate_block(b, spec, PALETTE)
    kinds = {v.kind for v in vs}
    _ok(ViolationKind.MISSING_IMAGE in kinds, "missing image not flagged")
    _ok(ViolationKind.BAD_COLOR in kinds, "bad color not flagged")


def test_document_structural_violations():
    blocks = [
        FilledBlock("headline", BlockType.HEADING, "Cutting idle fleet hours now"),
        FilledBlock("rogue", BlockType.BODY, "this block is not in the template at all today"),
        # 'body' and 'hero' missing
    ]
    vs = validate_document(blocks, TEMPLATE)
    kinds = {(v.block_id, v.kind) for v in vs}
    _ok(("body", ViolationKind.MISSING_BLOCK) in kinds, "missing body not flagged")
    _ok(("hero", ViolationKind.MISSING_BLOCK) in kinds, "missing hero not flagged")
    _ok(("rogue", ViolationKind.UNKNOWN_BLOCK) in kinds, "unknown block not flagged")


def test_happy_path_is_ok():
    body = ("Your fleet loses hours to idle vehicles waiting on manual dispatch. "
            "Our routing engine assigns jobs automatically, cutting that idle time and "
            "lifting on-time delivery, so your drivers spend more of the day moving freight "
            "and less of it parked, waiting for someone to tell them where to go next today.")
    blocks = [
        FilledBlock("headline", BlockType.HEADING, "Cutting idle fleet hours with AI", color="0FB5A6"),
        FilledBlock("body", BlockType.BODY, body),
        FilledBlock("hero", BlockType.CAPTION, "Fleet moving at dawn", image_ref="recv_logo_01"),
    ]
    _ok(constraints_ok(blocks, TEMPLATE), f"expected OK, got {validate_document(blocks, TEMPLATE)}")


def test_repair_instruction_directions():
    long_b = FilledBlock("headline", BlockType.HEADING, "one two three four five six seven eight nine")
    v = validate_block(long_b, TEMPLATE.block("headline"), PALETTE)[0]
    instr = repair_instruction(long_b, v)
    _ok("Shorten" in instr and "8 words" in instr, "shorten instruction wrong")

    short_b = FilledBlock("body", BlockType.BODY, "too short body")
    v2 = validate_block(short_b, TEMPLATE.block("body"), PALETTE)[0]
    instr2 = repair_instruction(short_b, v2)
    _ok("Expand" in instr2 and "awkward whitespace" in instr2, "expand instruction wrong")


def test_truncate_prefers_sentence_boundary():
    text = "First sentence is short. Second sentence makes it go well over the tight budget for sure."
    out = truncate_to_words(text, 5)
    _ok(out == "First sentence is short.", f"should keep first sentence, got: {out!r}")
    _ok(count_words(out) <= 5, "must be within budget")


def test_truncate_hard_cut_when_no_sentence_fits():
    text = "Anextremelylongrunonwithoutpunctuation " * 0 + "word " * 20
    out = truncate_to_words(text.strip(), 5)
    _ok(count_words(out) <= 5, f"hard cut must respect budget, got {count_words(out)}")
    _ok(out.endswith("\u2026"), "hard cut should add ellipsis")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} constraint-core tests passed.")


if __name__ == "__main__":
    _run_all()
