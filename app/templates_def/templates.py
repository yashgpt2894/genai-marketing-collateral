"""
Template registry.

Templates are the *hard contract* the generated article must satisfy. In
production these would live in Firestore / a CMS and be versioned; here we
define one realistic one-pager plus a denser two-column variant in code.

Theme colors and the palette mirror the kind of brand constraint an agency
template imposes ("headings in the brand teal; nothing off-palette").
"""
from __future__ import annotations

from app.constraints_core import BlockSpec, BlockType, TemplateSpec

# A neutral demo palette (hex, no '#'). Swap per client brand.
_PALETTE = ("0FB5A6", "10151E", "475569", "FFFFFF")

ONE_PAGER_V1 = TemplateSpec(
    id="one_pager_v1",
    name="One-pager bridge article",
    palette=_PALETTE,
    blocks=(
        BlockSpec("headline", BlockType.HEADING, min_words=4, max_words=9, theme_color="0FB5A6"),
        BlockSpec("subhead", BlockType.SUBHEADING, min_words=8, max_words=20),
        BlockSpec("hero", BlockType.CAPTION, min_words=3, max_words=12, image_placeholder=True, image_role="photo"),
        BlockSpec("body", BlockType.BODY, min_words=55, max_words=95),
        BlockSpec("cta", BlockType.CTA, min_words=4, max_words=14, theme_color="0FB5A6"),
    ),
)

TWO_COLUMN_V1 = TemplateSpec(
    id="two_column_v1",
    name="Two-column feature",
    palette=_PALETTE,
    blocks=(
        BlockSpec("headline", BlockType.HEADING, min_words=4, max_words=10, theme_color="0FB5A6"),
        BlockSpec("standfirst", BlockType.SUBHEADING, min_words=12, max_words=28),
        BlockSpec("col_left", BlockType.BODY, min_words=45, max_words=80),
        BlockSpec("col_right", BlockType.BODY, min_words=45, max_words=80),
        BlockSpec("pull_quote", BlockType.CAPTION, min_words=6, max_words=18),
        BlockSpec("cta", BlockType.CTA, min_words=4, max_words=14, theme_color="0FB5A6"),
    ),
)

FEATURE_V1 = TemplateSpec(
    id="feature_v1",
    name="Feature one-pager (logo + hero + chart)",
    palette=_PALETTE,
    blocks=(
        BlockSpec("headline", BlockType.HEADING, min_words=4, max_words=10, theme_color="0FB5A6"),
        BlockSpec("standfirst", BlockType.SUBHEADING, min_words=14, max_words=30),
        BlockSpec("logo", BlockType.CAPTION, min_words=2, max_words=8, image_placeholder=True, image_role="logo"),
        BlockSpec("hero", BlockType.CAPTION, min_words=4, max_words=16, image_placeholder=True, image_role="photo"),
        BlockSpec("intro", BlockType.BODY, min_words=40, max_words=80),
        BlockSpec("detail", BlockType.BODY, min_words=40, max_words=80),
        BlockSpec("proof", BlockType.CAPTION, min_words=6, max_words=20, image_placeholder=True, image_role="chart"),
        BlockSpec("cta", BlockType.CTA, min_words=4, max_words=16, theme_color="0FB5A6"),
    ),
)

# Monochrome executive palette — black / charcoal / slate / white (no accent colour).
_MONO = ("14161B", "23262E", "565C68", "FFFFFF")

EXECUTIVE_MONO_V1 = TemplateSpec(
    id="executive_mono_v1",
    name="Monochrome executive (2-page, photo-rich)",
    palette=_MONO,
    blocks=(
        BlockSpec("headline", BlockType.HEADING, min_words=4, max_words=10, theme_color="14161B"),
        BlockSpec("standfirst", BlockType.SUBHEADING, min_words=12, max_words=28),
        BlockSpec("hero", BlockType.CAPTION, min_words=2, max_words=12, image_placeholder=True, image_role="photo"),
        BlockSpec("intro", BlockType.BODY, min_words=55, max_words=95),
        BlockSpec("section", BlockType.HEADING, min_words=3, max_words=9, theme_color="14161B"),
        BlockSpec("body", BlockType.BODY, min_words=70, max_words=130),
        BlockSpec("shot_a", BlockType.CAPTION, min_words=2, max_words=10, image_placeholder=True, image_role="photo"),
        BlockSpec("shot_b", BlockType.CAPTION, min_words=2, max_words=10, image_placeholder=True, image_role="photo"),
        BlockSpec("shot_c", BlockType.CAPTION, min_words=2, max_words=12, image_placeholder=True, image_role="photo"),
        BlockSpec("quote", BlockType.CAPTION, min_words=8, max_words=24),  # pull-quote (no image)
        BlockSpec("cta", BlockType.CTA, min_words=6, max_words=16, theme_color="14161B"),
    ),
)

_REGISTRY: dict[str, TemplateSpec] = {
    ONE_PAGER_V1.id: ONE_PAGER_V1,
    TWO_COLUMN_V1.id: TWO_COLUMN_V1,
    FEATURE_V1.id: FEATURE_V1,
    EXECUTIVE_MONO_V1.id: EXECUTIVE_MONO_V1,
}

# template definitions are versioned alongside prompts/models for reproducibility
TEMPLATE_VERSION = "t1"


def get_template(template_id: str) -> TemplateSpec:
    try:
        return _REGISTRY[template_id]
    except KeyError:
        raise KeyError(f"unknown template '{template_id}'. known: {list(_REGISTRY)}")


def list_templates() -> list[TemplateSpec]:
    return list(_REGISTRY.values())
