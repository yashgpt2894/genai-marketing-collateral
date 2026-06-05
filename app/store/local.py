"""
Local filesystem backend (dev default). Implements the Store interface so the
app code is identical whether this or the cloud backend is active.

Layout on disk:
  data/
    <tenant>/
      <pair_id>/
        uploads/        raw PDFs (sender_*.pdf, receiver_*.pdf)
        assets/         extracted images/logos (<asset_id>.<ext>)
        sender.json     CompanyBrief
        receiver.json   CompanyBrief
        jobs.json       upload/parse job status
        outputs/        generated ArticleJSON results (<request_id>.json)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.schemas import ArticleJSON, CompanyBrief, TemplateModel
from app.store.base import content_type_for

_SAFE = re.compile(r"[^a-zA-Z0-9_\-.]")


def _safe(s: str, *, allow_dot: bool = False) -> str:
    pat = _SAFE if allow_dot else re.compile(r"[^a-zA-Z0-9_\-]")
    out = pat.sub("_", s).strip("_")
    if not out:
        raise ValueError(f"invalid storage key: {s!r}")
    return out


class LocalStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or get_settings().data_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _pair_dir(self, tenant: str, pair_id: str) -> Path:
        d = self.root / _safe(tenant) / _safe(pair_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- uploads ---------------------------------------------------------------
    def save_upload(self, tenant: str, pair_id: str, role: str, filename: str, data: bytes) -> str:
        key = f"{_safe(role)}_{_safe(Path(filename).name, allow_dot=True)}"  # idempotent per role+filename
        d = self._pair_dir(tenant, pair_id) / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        (d / key).write_bytes(data)
        return key

    def load_upload(self, tenant: str, pair_id: str, key: str) -> bytes:
        return (self._pair_dir(tenant, pair_id) / "uploads" / _safe(key, allow_dot=True)).read_bytes()

    def list_uploads(self, tenant: str, pair_id: str, role: str) -> list[str]:
        d = self._pair_dir(tenant, pair_id) / "uploads"
        return sorted(p.name for p in d.glob(f"{_safe(role)}_*")) if d.exists() else []

    # -- assets ----------------------------------------------------------------
    def save_asset(self, tenant: str, pair_id: str, asset_id: str, ext: str, data: bytes) -> str:
        d = self._pair_dir(tenant, pair_id) / "assets"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{_safe(asset_id)}.{_safe(ext)}").write_bytes(data)
        return asset_id

    def load_asset(self, tenant: str, pair_id: str, asset_id: str) -> Optional[tuple[bytes, str]]:
        d = self._pair_dir(tenant, pair_id) / "assets"
        matches = sorted(d.glob(f"{_safe(asset_id)}.*")) if d.exists() else []
        if not matches:
            return None
        p = matches[0]
        return p.read_bytes(), content_type_for(p.suffix)

    def delete_asset(self, tenant: str, pair_id: str, asset_id: str) -> bool:
        d = self._pair_dir(tenant, pair_id) / "assets"
        matches = sorted(d.glob(f"{_safe(asset_id)}.*")) if d.exists() else []
        for p in matches:
            p.unlink()
        return bool(matches)

    # -- briefs ----------------------------------------------------------------
    def save_brief(self, tenant: str, pair_id: str, brief: CompanyBrief) -> None:
        (self._pair_dir(tenant, pair_id) / f"{_safe(brief.role)}.json").write_text(brief.model_dump_json(indent=2))

    def load_brief(self, tenant: str, pair_id: str, role: str) -> Optional[CompanyBrief]:
        p = self._pair_dir(tenant, pair_id) / f"{_safe(role)}.json"
        return CompanyBrief.model_validate_json(p.read_text()) if p.exists() else None

    def ready_roles(self, tenant: str, pair_id: str) -> list[str]:
        d = self._pair_dir(tenant, pair_id)
        return [r for r in ("sender", "receiver") if (d / f"{r}.json").exists()]

    # -- jobs ------------------------------------------------------------------
    def set_job(self, tenant: str, pair_id: str, job_id: str, status: str, message: str = "") -> None:
        p = self._pair_dir(tenant, pair_id) / "jobs.json"
        jobs = json.loads(p.read_text()) if p.exists() else {}
        jobs[job_id] = {"status": status, "message": message}
        p.write_text(json.dumps(jobs, indent=2))

    def get_job(self, tenant: str, pair_id: str, job_id: str) -> Optional[dict]:
        p = self._pair_dir(tenant, pair_id) / "jobs.json"
        return json.loads(p.read_text()).get(job_id) if p.exists() else None

    # -- outputs ---------------------------------------------------------------
    def save_output(self, tenant: str, pair_id: str, result: ArticleJSON) -> None:
        d = self._pair_dir(tenant, pair_id) / "outputs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{_safe(result.meta.request_id)}.json").write_text(result.model_dump_json(indent=2))

    def load_output(self, tenant: str, pair_id: str, request_id: str) -> Optional[ArticleJSON]:
        p = self._pair_dir(tenant, pair_id) / "outputs" / f"{_safe(request_id)}.json"
        return ArticleJSON.model_validate_json(p.read_text()) if p.exists() else None

    # -- custom templates (tenant-scoped) --------------------------------------
    def _templates_dir(self, tenant: str) -> Path:
        d = self.root / _safe(tenant) / "templates"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_template(self, tenant: str, template: TemplateModel) -> None:
        (self._templates_dir(tenant) / f"{_safe(template.id)}.json").write_text(
            template.model_dump_json(indent=2))

    def list_templates(self, tenant: str) -> list[TemplateModel]:
        d = self._templates_dir(tenant)
        out: list[TemplateModel] = []
        for p in sorted(d.glob("*.json")):
            try:
                out.append(TemplateModel.model_validate_json(p.read_text()))
            except Exception:
                continue  # skip a corrupt/legacy file rather than fail the whole list
        return out

    def load_template(self, tenant: str, template_id: str) -> Optional[TemplateModel]:
        p = self._templates_dir(tenant) / f"{_safe(template_id)}.json"
        return TemplateModel.model_validate_json(p.read_text()) if p.exists() else None

    def delete_template(self, tenant: str, template_id: str) -> bool:
        p = self._templates_dir(tenant) / f"{_safe(template_id)}.json"
        if p.exists():
            p.unlink()
            return True
        return False
