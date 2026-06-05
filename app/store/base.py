"""
Storage interface — the seam between the app and where bytes/documents live.

Deliberately **not** filesystem-shaped: methods take/return bytes and typed
documents keyed by (tenant, pair_id, ...), never `Path`. That's what lets the
same app run on the local filesystem (dev) or on GCS + Firestore (production)
with only a config flag — and what makes the system multi-tenant from the
ground up (tenant_id is the first key on every operation, so one tenant's data
can never be addressed under another's).

  uploads (raw PDFs)        -> GCS blob   / local file
  assets  (logos, images)   -> GCS blob   / local file
  briefs · jobs · outputs   -> Firestore doc / local JSON
"""
from __future__ import annotations

from typing import Optional, Protocol

from app.schemas import ArticleJSON, CompanyBrief, TemplateModel

_CONTENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}


def content_type_for(ext: str) -> str:
    return _CONTENT_TYPES.get(ext.lower().lstrip("."), "application/octet-stream")


class Store(Protocol):
    """All operations are scoped by tenant first, then pair_id."""

    # -- uploads (raw PDFs) ----------------------------------------------------
    def save_upload(self, tenant: str, pair_id: str, role: str, filename: str, data: bytes) -> str:
        """Persist a raw PDF; return its storage key."""
        ...

    def load_upload(self, tenant: str, pair_id: str, key: str) -> bytes:
        ...

    def list_uploads(self, tenant: str, pair_id: str, role: str) -> list[str]:
        """Keys of the uploaded PDFs for a role (idempotent per role+filename)."""
        ...

    # -- assets (extracted images/logos) ---------------------------------------
    def save_asset(self, tenant: str, pair_id: str, asset_id: str, ext: str, data: bytes) -> str:
        """Persist an extracted image; return a stable reference (key/uri)."""
        ...

    def load_asset(self, tenant: str, pair_id: str, asset_id: str) -> Optional[tuple[bytes, str]]:
        """Return (bytes, content_type) or None."""
        ...

    def delete_asset(self, tenant: str, pair_id: str, asset_id: str) -> bool:
        """Delete an extracted asset; return True if it existed."""
        ...

    # -- briefs ----------------------------------------------------------------
    def save_brief(self, tenant: str, pair_id: str, brief: CompanyBrief) -> None: ...
    def load_brief(self, tenant: str, pair_id: str, role: str) -> Optional[CompanyBrief]: ...
    def ready_roles(self, tenant: str, pair_id: str) -> list[str]: ...

    # -- jobs ------------------------------------------------------------------
    def set_job(self, tenant: str, pair_id: str, job_id: str, status: str, message: str = "") -> None: ...
    def get_job(self, tenant: str, pair_id: str, job_id: str) -> Optional[dict]: ...

    # -- outputs ---------------------------------------------------------------
    def save_output(self, tenant: str, pair_id: str, result: ArticleJSON) -> None: ...
    def load_output(self, tenant: str, pair_id: str, request_id: str) -> Optional[ArticleJSON]: ...

    # -- custom templates (tenant-scoped; built-ins live in templates_def) ------
    def save_template(self, tenant: str, template: TemplateModel) -> None: ...
    def list_templates(self, tenant: str) -> list[TemplateModel]: ...
    def load_template(self, tenant: str, template_id: str) -> Optional[TemplateModel]: ...
    def delete_template(self, tenant: str, template_id: str) -> bool: ...
