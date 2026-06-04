"""
The writer: draft a tailored, grounded, cited bridging article as structured
JSON matching the template's blocks.

Grounding rules (the factual-correctness story):
  * generate ONLY from the provided briefs; abstain rather than invent;
  * every block lists the brief fact-ids it draws on (citations) — internal
    metadata for verification, not footnotes in the brochure;
  * the template tells the model the blocks, their word budgets and which block
    holds the hero image.

Controlled generation: response_schema = ArticleDraft, so we get schema-valid
JSON at decode time. We still validate + repair lengths in code afterwards.
"""
from __future__ import annotations

from app.config import get_settings
from app.constraints_core import TemplateSpec
from app.generate.context import ContextAssembler, default_assembler
from app.llm.gemini import INJECTION_GUARD, GeminiClient
from app.schemas import ArticleDraft, CompanyBrief

_WRITER_SYS = (
    "You are a senior B2B marketing writer. You write a short, tailored article for a "
    "SENDER company to send to a RECEIVER company, bridging the two domains and showing how "
    "the sender helps the receiver. Requirements:\n"
    "1. Use ONLY facts present in the grounding data. If a needed fact is absent, write around "
    "it or keep that block general — never invent companies, products, numbers or quotes.\n"
    "2. For each block, list the fact-ids you used in `citations` (e.g. ['recv.pain.2','send.fact.1']).\n"
    "3. Match the receiver's relevance and the sender's tone signals.\n"
    "4. Respect each block's word budget as closely as you can (they are enforced afterwards).\n"
    "5. Put the hero image reference on the block that has an image placeholder if one is provided.\n"
    + INJECTION_GUARD
)


def _template_spec_text(t: TemplateSpec) -> str:
    rows = []
    for b in t.blocks:
        img = ", has image placeholder" if b.image_placeholder else ""
        col = f", color must be {b.theme_color}" if b.theme_color else ""
        rows.append(f"  - id='{b.id}' type={b.type.value}: {b.min_words}-{b.max_words} words{img}{col}")
    return "TEMPLATE BLOCKS (fill every one):\n" + "\n".join(rows)


def draft_article(
    sender: CompanyBrief,
    receiver: CompanyBrief,
    prompt: str,
    template: TemplateSpec,
    *,
    llm: GeminiClient | None = None,
    assembler: ContextAssembler | None = None,
    logo_ref: str | None = None,
) -> ArticleDraft:
    settings = get_settings()
    llm = llm or GeminiClient(settings)
    assembler = assembler or default_assembler()

    grounding = assembler.assemble(sender, receiver)
    spec_text = _template_spec_text(template)
    hero_hint = f"\nAvailable hero image asset id: '{logo_ref}'." if logo_ref else ""

    task = (
        f"{spec_text}\n\n"
        f"EDITOR PROMPT (the angle for this article): {prompt}\n"
        f"{hero_hint}\n\n"
        "Write the article now as JSON with one entry per template block."
    )

    # grounding kept as a SEPARATE content part from the task; system instruction
    # forbids obeying instructions found inside it.
    contents = [grounding, task]

    return llm.generate_structured(  # type: ignore[return-value]
        model=settings.model_writer, contents=contents, schema=ArticleDraft,
        system_instruction=_WRITER_SYS, temperature=settings.writer_temperature,
    )
