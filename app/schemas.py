"""
Pydantic v2 schemas = the API wire contract.

These are kept separate from app/constraints_core.py on purpose: the core engine
is framework-free and unit-testable; these models handle (de)serialization and
controlled-generation response schemas for Gemini. Small converters bridge the two.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.constraints_core import (
    BlockSpec, BlockType, FilledBlock, TemplateSpec,
)

# ----------------------------------------------------------------------------- 
# Company briefs (grounding source) — every fact carries a source pointer
# -----------------------------------------------------------------------------

FactKind = Literal["offering", "value_prop", "pain_point", "stat", "tone", "fact"]


class Fact(BaseModel):
    id: str = Field(description="stable id, e.g. 'recv.pain.2' — referenced by article citations")
    text: str
    kind: FactKind = "fact"
    source_page: Optional[int] = Field(default=None, description="page in the source PDF")


class ImageAsset(BaseModel):
    id: str
    kind: Literal["logo", "image"] = "image"
    path: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    width: Optional[int] = None
    height: Optional[int] = None


class CompanyBrief(BaseModel):
    """The distilled, reusable, source-tagged representation of one company."""
    role: Literal["sender", "receiver"]
    name: str
    industry: Optional[str] = None
    offerings: list[str] = Field(default_factory=list)
    value_props: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    tone_signals: list[str] = Field(default_factory=list)
    key_stats: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    logo_asset: Optional[str] = None
    images: list[ImageAsset] = Field(default_factory=list)


# Slim schema used as Gemini's response_schema for multimodal PDF extraction.
# (No assets here — those are pulled deterministically by PyMuPDF, not the VLM.)
class BriefExtraction(BaseModel):
    name: str
    industry: Optional[str] = None
    offerings: list[str] = Field(default_factory=list)
    value_props: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    tone_signals: list[str] = Field(default_factory=list)
    key_stats: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


# ----------------------------------------------------------------------------- 
# Templates
# -----------------------------------------------------------------------------


class BlockSpecModel(BaseModel):
    id: str
    type: BlockType
    min_words: int
    max_words: int
    image_placeholder: bool = False
    theme_color: Optional[str] = None


_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")


class TemplateModel(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
                    description="slug id: lowercase letters/digits, '-' or '_'")
    name: str = Field(min_length=1, max_length=120)
    blocks: list[BlockSpecModel]
    palette: list[str]

    @model_validator(mode="after")
    def _check(self) -> "TemplateModel":
        if not self.blocks:
            raise ValueError("template needs at least one block")
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block ids must be unique")
        if not self.palette:
            raise ValueError("palette needs at least one colour")
        for c in self.palette:
            if not _HEX6.match(c):
                raise ValueError(f"palette colour '{c}' must be 6-digit hex (no '#')")
        for b in self.blocks:
            if b.min_words < 0 or b.max_words < 1 or b.max_words < b.min_words:
                raise ValueError(f"block '{b.id}': need 0 <= min_words <= max_words and max_words >= 1")
            if b.theme_color is not None:
                if not _HEX6.match(b.theme_color):
                    raise ValueError(f"block '{b.id}': theme_color '{b.theme_color}' must be 6-digit hex")
                if b.theme_color not in self.palette:
                    raise ValueError(f"block '{b.id}': theme_color must be one of the palette colours")
        return self

    @classmethod
    def from_spec(cls, t: TemplateSpec) -> "TemplateModel":
        return cls(
            id=t.id, name=t.name, palette=list(t.palette),
            blocks=[BlockSpecModel(id=b.id, type=b.type, min_words=b.min_words,
                                   max_words=b.max_words, image_placeholder=b.image_placeholder,
                                   theme_color=b.theme_color) for b in t.blocks],
        )

    def to_spec(self) -> TemplateSpec:
        return TemplateSpec(
            id=self.id, name=self.name, palette=tuple(self.palette),
            blocks=tuple(BlockSpec(id=b.id, type=b.type, min_words=b.min_words,
                                   max_words=b.max_words, image_placeholder=b.image_placeholder,
                                   theme_color=b.theme_color) for b in self.blocks),
        )


# ----------------------------------------------------------------------------- 
# Article output
# -----------------------------------------------------------------------------


class ArticleBlock(BaseModel):
    id: str
    type: BlockType
    text: str
    words: int = 0
    max_words: Optional[int] = None
    min_words: Optional[int] = None
    citations: list[str] = Field(default_factory=list)
    image_ref: Optional[str] = None
    color: Optional[str] = None

    def to_filled(self) -> FilledBlock:
        return FilledBlock(id=self.id, type=self.type, text=self.text,
                           citations=list(self.citations), image_ref=self.image_ref,
                           color=self.color)


# What the writer model emits (controlled generation target).
class DraftBlock(BaseModel):
    id: str
    type: BlockType
    text: str
    citations: list[str] = Field(default_factory=list)
    image_ref: Optional[str] = None
    color: Optional[str] = None


class ArticleDraft(BaseModel):
    blocks: list[DraftBlock]


class ConstraintReport(BaseModel):
    ok: bool
    blocks_within_limits: int
    total_blocks: int
    violations: list[str] = Field(default_factory=list)
    repaired_blocks: list[str] = Field(default_factory=list)
    truncated_blocks: list[str] = Field(default_factory=list)


class FaithfulnessReport(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="fraction of claims supported by the briefs")
    supported: int = 0
    total_claims: int = 0
    unsupported_claims: list[str] = Field(default_factory=list)
    checked: bool = True


class GenerationMeta(BaseModel):
    request_id: str
    model_writer: str
    prompt_version: str
    template_id: str
    repair_iterations: int = 0
    # cost telemetry (summed across every model call in this generation)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = Field(default=0.0, description="estimated USD cost, per-model pricing")


class ArticleJSON(BaseModel):
    """The final structured output the renderer consumes."""
    pair_id: str
    template_id: str
    blocks: list[ArticleBlock]
    assets: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    constraints_ok: bool
    constraints: ConstraintReport
    faithfulness: FaithfulnessReport
    meta: GenerationMeta


# ----------------------------------------------------------------------------- 
# Endpoint request/response bodies
# -----------------------------------------------------------------------------


class UploadResponse(BaseModel):
    pair_id: str
    job_id: str
    status: Literal["processing", "done", "error"]
    message: str
    briefs: list[str] = Field(default_factory=list)  # roles that are ready


class GenerateRequest(BaseModel):
    pair_id: str
    prompt: str = Field(min_length=3, description="what the article should say / angle")
    template_id: str = "one_pager_v1"


class BriefsResponse(BaseModel):
    pair_id: str
    sender: Optional[CompanyBrief] = None
    receiver: Optional[CompanyBrief] = None
