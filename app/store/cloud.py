"""
Cloud backend: **GCS** for blobs (PDFs + assets) and **Firestore** for documents
(briefs, jobs, outputs). Same Store interface as LocalStore, so nothing upstream
changes — flip STORE_BACKEND=cloud and set GCS_BUCKET.

The google-cloud SDKs are imported lazily (in __init__), so the app still runs
locally without them installed; you only need them when this backend is selected.

Key scheme (tenant-first, so isolation is structural):
  GCS:       <tenant>/<pair>/uploads/<key>   ·   <tenant>/<pair>/assets/<id>.<ext>
  Firestore: tenants/<tenant>/pairs/<pair>/{briefs|jobs|outputs}/<doc-id>
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import Settings, get_settings
from app.schemas import ArticleJSON, CompanyBrief, TemplateModel
from app.store.base import content_type_for

_SEG = re.compile(r"[^a-zA-Z0-9_\-.]")


def _seg(s: str) -> str:
    out = _SEG.sub("_", s).strip("_")
    if not out:
        raise ValueError(f"invalid storage segment: {s!r}")
    return out


class CloudStore:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.s = settings or get_settings()
        if not self.s.gcs_bucket:
            raise ValueError("STORE_BACKEND=cloud requires GCS_BUCKET to be set")
        try:
            from google.cloud import firestore, storage
        except Exception as e:  # pragma: no cover - only when cloud backend is used
            raise RuntimeError(
                "Cloud store needs google-cloud-firestore and google-cloud-storage "
                "(`pip install '.[cloud]'`)."
            ) from e
        self._fs = firestore.Client(database=self.s.firestore_database)
        self._bucket = storage.Client().bucket(self.s.gcs_bucket)

    # -- key helpers -----------------------------------------------------------
    def _prefix(self, tenant: str, pair_id: str) -> str:
        return f"{_seg(tenant)}/{_seg(pair_id)}"

    def _coll(self, tenant: str, pair_id: str, kind: str) -> Any:
        return (self._fs.collection("tenants").document(_seg(tenant))
                .collection("pairs").document(_seg(pair_id)).collection(kind))

    # -- uploads ---------------------------------------------------------------
    def save_upload(self, tenant: str, pair_id: str, role: str, filename: str, data: bytes) -> str:
        from pathlib import Path
        key = f"{_seg(role)}_{_seg(Path(filename).name)}"
        self._bucket.blob(f"{self._prefix(tenant, pair_id)}/uploads/{key}").upload_from_string(
            data, content_type="application/pdf")
        return key

    def load_upload(self, tenant: str, pair_id: str, key: str) -> bytes:
        return self._bucket.blob(f"{self._prefix(tenant, pair_id)}/uploads/{_seg(key)}").download_as_bytes()

    def list_uploads(self, tenant: str, pair_id: str, role: str) -> list[str]:
        prefix = f"{self._prefix(tenant, pair_id)}/uploads/{_seg(role)}_"
        return sorted(b.name.split("/")[-1] for b in self._bucket.list_blobs(prefix=prefix))

    # -- assets ----------------------------------------------------------------
    def save_asset(self, tenant: str, pair_id: str, asset_id: str, ext: str, data: bytes) -> str:
        path = f"{self._prefix(tenant, pair_id)}/assets/{_seg(asset_id)}.{_seg(ext)}"
        self._bucket.blob(path).upload_from_string(data, content_type=content_type_for(ext))
        return asset_id

    def load_asset(self, tenant: str, pair_id: str, asset_id: str) -> Optional[tuple[bytes, str]]:
        prefix = f"{self._prefix(tenant, pair_id)}/assets/{_seg(asset_id)}."
        # newest first, so a re-uploaded asset wins over a stale one with a different extension
        blobs = sorted(self._bucket.list_blobs(prefix=prefix),
                       key=lambda b: b.updated or b.time_created, reverse=True)
        if not blobs:
            return None
        b = blobs[0]
        return b.download_as_bytes(), (b.content_type or content_type_for(b.name.rsplit(".", 1)[-1]))

    def delete_asset(self, tenant: str, pair_id: str, asset_id: str) -> bool:
        prefix = f"{self._prefix(tenant, pair_id)}/assets/{_seg(asset_id)}."
        blobs = list(self._bucket.list_blobs(prefix=prefix))
        for b in blobs:
            b.delete()
        return bool(blobs)

    # -- briefs ----------------------------------------------------------------
    def save_brief(self, tenant: str, pair_id: str, brief: CompanyBrief) -> None:
        self._coll(tenant, pair_id, "briefs").document(_seg(brief.role)).set(brief.model_dump())

    def load_brief(self, tenant: str, pair_id: str, role: str) -> Optional[CompanyBrief]:
        snap = self._coll(tenant, pair_id, "briefs").document(_seg(role)).get()
        return CompanyBrief.model_validate(snap.to_dict()) if snap.exists else None

    def ready_roles(self, tenant: str, pair_id: str) -> list[str]:
        return [r for r in ("sender", "receiver")
                if self._coll(tenant, pair_id, "briefs").document(r).get().exists]

    # -- jobs ------------------------------------------------------------------
    def set_job(self, tenant: str, pair_id: str, job_id: str, status: str,
                message: str = "", kind: str = "") -> None:
        doc = self._coll(tenant, pair_id, "jobs").document(_seg(job_id))
        now = datetime.now(timezone.utc).isoformat()
        prev = doc.get().to_dict() or {}
        doc.set({
            "status": status, "message": message,
            "kind": kind or prev.get("kind", ""),          # set on create, preserved on update
            "created_at": prev.get("created_at", now),
            "updated_at": now,
        })

    def get_job(self, tenant: str, pair_id: str, job_id: str) -> Optional[dict]:
        snap = self._coll(tenant, pair_id, "jobs").document(_seg(job_id)).get()
        return snap.to_dict() if snap.exists else None

    def list_jobs(self, tenant: str, pair_id: str) -> list[dict]:
        out = [{"job_id": d.id, **(d.to_dict() or {})}
               for d in self._coll(tenant, pair_id, "jobs").stream()]
        out.sort(key=lambda j: j.get("created_at", ""), reverse=True)  # newest first
        return out

    # -- outputs ---------------------------------------------------------------
    def save_output(self, tenant: str, pair_id: str, result: ArticleJSON) -> None:
        self._coll(tenant, pair_id, "outputs").document(_seg(result.meta.request_id)).set(result.model_dump())

    def load_output(self, tenant: str, pair_id: str, request_id: str) -> Optional[ArticleJSON]:
        snap = self._coll(tenant, pair_id, "outputs").document(_seg(request_id)).get()
        return ArticleJSON.model_validate(snap.to_dict()) if snap.exists else None

    # -- custom templates (tenant-scoped) --------------------------------------
    def _templates(self, tenant: str) -> Any:
        return self._fs.collection("tenants").document(_seg(tenant)).collection("templates")

    def save_template(self, tenant: str, template: TemplateModel) -> None:
        self._templates(tenant).document(_seg(template.id)).set(template.model_dump())

    def list_templates(self, tenant: str) -> list[TemplateModel]:
        return [TemplateModel.model_validate(d.to_dict()) for d in self._templates(tenant).stream()]

    def load_template(self, tenant: str, template_id: str) -> Optional[TemplateModel]:
        snap = self._templates(tenant).document(_seg(template_id)).get()
        return TemplateModel.model_validate(snap.to_dict()) if snap.exists else None

    def delete_template(self, tenant: str, template_id: str) -> bool:
        doc = self._templates(tenant).document(_seg(template_id))
        existed = doc.get().exists
        doc.delete()
        return existed
