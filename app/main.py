"""
FastAPI app — the two endpoints from the brief, plus supporting routes and the UI.

  POST /companies/{pair_id}/documents   upload sender/receiver PDFs -> parse -> briefs
  POST /generate                        {pair_id, prompt, template_id} -> ArticleJSON

  GET  /templates            list templates
  GET  /companies/{pair_id}  the stored briefs
  GET  /healthz              liveness + whether the LLM is configured
  GET  /                     the demo UI (static)

Design notes:
  * Storage is pluggable (LocalStore | CloudStore) via get_store(); all calls are
    tenant-scoped. tenant_id comes from the X-Tenant-ID header (default from config
    until real auth supplies it) — so the API is multi-tenant from the ground up.
  * Parsing can be slow, so uploads run in a BackgroundTask and return a job id
    (Pub/Sub + Cloud Run jobs in production).
  * Uploads are idempotent per (pair_id, role, filename).
  * Every response carries an X-Request-ID; generation stamps model/prompt/template
    versions for reproducibility.
  * If the LLM isn't configured, mutating endpoints return 503 with how to fix it —
    the service never fabricates output.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException,
    Path as PathParam, Request, Response, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_identity
from app.cache import brief_fingerprint, gen_cache_key, get_cache, load_cached
from app.config import get_settings
from app.generate.pipeline import run_generation
from app.metrics import emit_generation_metric
from app.ingest.brief_builder import build_brief
from app.llm.gemini import GeminiClient, LLMError, LLMNotConfigured
from app.schemas import BriefsResponse, GenerateRequest, TemplateModel, UploadResponse
from app.store import get_store
from app.templates_def.templates import get_template, list_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("collateral.api")

app = FastAPI(title="Generative Marketing Collateral", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

store = get_store()
llm = GeminiClient()

_STATIC = Path(__file__).resolve().parent.parent / "static"


def get_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    """Tenant from the auth-supplied header; falls back to the configured default."""
    return x_tenant_id or get_settings().default_tenant


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/healthz")
def healthz():
    s = get_settings()
    return {
        "status": "ok",
        "llm_configured": s.llm_configured,
        "store": s.store_backend,
        "models": {"writer": s.model_writer, "parser": s.model_parser, "cheap": s.model_cheap},
        "auth": "vertex" if s.use_vertex else ("developer_api" if s.gemini_api_key else "none"),
        "auth_mode": s.auth_mode,
    }


@app.get("/templates", response_model=list[TemplateModel])
def templates(principal: str = Depends(require_identity)):
    return [TemplateModel.from_spec(t) for t in list_templates()]


@app.get("/companies/{pair_id}", response_model=BriefsResponse)
def get_companies(pair_id: str = PathParam(...), tenant: str = Depends(get_tenant),
                  principal: str = Depends(require_identity)):
    return BriefsResponse(
        pair_id=pair_id,
        sender=store.load_brief(tenant, pair_id, "sender"),
        receiver=store.load_brief(tenant, pair_id, "receiver"),
    )


def _parse_job(tenant: str, pair_id: str, job_id: str, role: str, keys: list[str]):
    try:
        brief = build_brief(store, tenant, pair_id, role, keys, llm=llm)  # type: ignore[arg-type]
        store.save_brief(tenant, pair_id, brief)
        store.set_job(tenant, pair_id, job_id, "done", f"{role} brief ready ({len(brief.facts)} facts)")
        log.info("parsed %s for tenant=%s pair=%s (%d facts)", role, tenant, pair_id, len(brief.facts))
    except LLMNotConfigured as e:
        store.set_job(tenant, pair_id, job_id, "error", str(e))
    except Exception as e:
        log.exception("parse failed")
        store.set_job(tenant, pair_id, job_id, "error", f"{type(e).__name__}: {e}")


@app.post("/companies/{pair_id}/documents", response_model=UploadResponse)
async def upload_documents(
    background: BackgroundTasks,
    pair_id: str = PathParam(...),
    role: Literal["sender", "receiver"] = Form(...),
    files: list[UploadFile] = File(...),
    tenant: str = Depends(get_tenant),
    principal: str = Depends(require_identity),
):
    if not llm.configured:
        raise HTTPException(status_code=503, detail=(
            "LLM not configured — set GEMINI_API_KEY (or Vertex env) before uploading. See .env.example."
        ))
    if not files:
        raise HTTPException(status_code=422, detail="no files uploaded")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    keys: list[str] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=422, detail=f"only PDFs accepted, got '{f.filename}'")
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=(
                f"'{f.filename}' exceeds the {get_settings().max_upload_mb}MB upload limit"))
        keys.append(store.save_upload(tenant, pair_id, role, f.filename or "upload.pdf", data))

    job_id = uuid.uuid4().hex[:12]
    store.set_job(tenant, pair_id, job_id, "processing", f"parsing {len(keys)} file(s) for {role}")
    payload = {"tenant": tenant, "pair_id": pair_id, "job_id": job_id, "role": role, "keys": keys}
    if get_settings().parse_backend == "pubsub":
        from app.messaging import publish_parse
        publish_parse(payload)                       # Pub/Sub -> worker at POST /internal/parse
    else:
        background.add_task(_parse_job, tenant, pair_id, job_id, role, keys)  # in-process (local default)

    return UploadResponse(
        pair_id=pair_id, job_id=job_id, status="processing",
        message=f"Uploaded {len(keys)} file(s) for {role}; parsing in background.",
        briefs=store.ready_roles(tenant, pair_id),
    )


@app.get("/jobs/{pair_id}/{job_id}")
def job_status(pair_id: str, job_id: str, tenant: str = Depends(get_tenant),
               principal: str = Depends(require_identity)):
    job = store.get_job(tenant, pair_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"pair_id": pair_id, "job_id": job_id, **job, "briefs": store.ready_roles(tenant, pair_id)}


@app.post("/internal/parse", include_in_schema=False)
async def internal_parse(request: Request):
    """Pub/Sub *push* target (production parse worker). Decodes the job message and
    runs the same parse as the local path. In production the push subscription is
    locked to the Pub/Sub service account via an OIDC token verified here."""
    from app.messaging import decode_push
    try:
        p = decode_push(await request.json())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad Pub/Sub envelope: {e}")
    _parse_job(p["tenant"], p["pair_id"], p["job_id"], p["role"], p["keys"])
    return {"status": "ok"}


def _generate_job(tenant, pair_id, request_id, sender, receiver, prompt, template):
    """Heavy draft->verify->repair work, off the request path (Pub/Sub + worker in prod).
    Checks the result cache first (skips the model on a hit), then emits a metrics row."""
    cache = get_cache()
    ckey = gen_cache_key(tenant, pair_id, prompt, template.id, brief_fingerprint(sender, receiver))
    t0 = time.monotonic()
    try:
        cached = load_cached(cache, ckey)
        if cached is not None:
            cached.meta.request_id = request_id  # re-stamp so storage/poll keys match this job
            store.save_output(tenant, pair_id, cached)
            store.set_job(tenant, pair_id, request_id, "done", "article ready (cache hit)")
            log.info("cache hit %s tenant=%s pair=%s", request_id, tenant, pair_id)
            emit_generation_metric(tenant, pair_id, cached,
                                   latency_ms=int((time.monotonic() - t0) * 1000), cached=True)
            return

        result = run_generation(pair_id, sender, receiver, prompt, template, request_id, llm=llm)
        store.save_output(tenant, pair_id, result)
        store.set_job(tenant, pair_id, request_id, "done", "article ready")
        cache.set(ckey, result.model_dump_json(), get_settings().cache_ttl_seconds)
        log.info("generated %s tenant=%s pair=%s (%d tok, $%.4f)",
                 request_id, tenant, pair_id, result.meta.total_tokens, result.meta.cost_usd)
        emit_generation_metric(tenant, pair_id, result,
                               latency_ms=int((time.monotonic() - t0) * 1000), cached=False)
    except LLMNotConfigured as e:
        store.set_job(tenant, pair_id, request_id, "error", str(e))
    except LLMError as e:
        store.set_job(tenant, pair_id, request_id, "error", f"model error: {e}")
    except Exception as e:
        log.exception("generation failed")
        store.set_job(tenant, pair_id, request_id, "error", f"{type(e).__name__}: {e}")


@app.post("/generate", status_code=202)
def generate(req: GenerateRequest, background: BackgroundTasks,
             tenant: str = Depends(get_tenant), principal: str = Depends(require_identity),
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Accept the job and return a job_id immediately — never blocks on the model.
    Poll GET /generate/{pair_id}/{job_id} for the result. Send an Idempotency-Key
    header to make a retried POST return the same job instead of a duplicate."""
    if not llm.configured:
        raise HTTPException(status_code=503, detail=(
            "LLM not configured — set GEMINI_API_KEY (or Vertex env) before generating. See .env.example."
        ))
    sender = store.load_brief(tenant, req.pair_id, "sender")
    receiver = store.load_brief(tenant, req.pair_id, "receiver")
    if sender is None or receiver is None:
        missing = [r for r in ("sender", "receiver") if store.load_brief(tenant, req.pair_id, r) is None]
        raise HTTPException(status_code=409, detail=(
            f"briefs not ready for pair '{req.pair_id}': missing {missing}. "
            f"Upload documents for both roles first."
        ))
    try:
        template = get_template(req.template_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if idempotency_key:  # a retried POST with the same key returns the same job (no duplicate)
        request_id = "idem" + hashlib.sha1(
            f"{tenant}:{req.pair_id}:{idempotency_key}".encode()).hexdigest()[:12]
        existing = store.get_job(tenant, req.pair_id, request_id)
        if existing:
            return {"pair_id": req.pair_id, "job_id": request_id,
                    "status": existing.get("status", "processing"),
                    "poll": f"/generate/{req.pair_id}/{request_id}", "idempotent": True}
    else:
        request_id = uuid.uuid4().hex[:12]
    store.set_job(tenant, req.pair_id, request_id, "processing", f"generating article (by {principal})")
    background.add_task(_generate_job, tenant, req.pair_id, request_id, sender, receiver, req.prompt, template)
    return {"pair_id": req.pair_id, "job_id": request_id, "status": "processing",
            "poll": f"/generate/{req.pair_id}/{request_id}"}


@app.get("/generate/{pair_id}/{job_id}")
def generate_status(pair_id: str, job_id: str, tenant: str = Depends(get_tenant),
                    principal: str = Depends(require_identity)):
    """Poll a generation job. When done, returns the full ArticleJSON under `result`."""
    job = store.get_job(tenant, pair_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    status = job.get("status")
    if status == "done":
        out = store.load_output(tenant, pair_id, job_id)
        return JSONResponse({"status": "done", "result": out.model_dump() if out else None})
    if status == "error":
        return JSONResponse({"status": "error", "message": job.get("message", "")})
    return {"status": status or "processing", "message": job.get("message", "")}


@app.get("/assets/{pair_id}/{asset_id}")
def get_asset(pair_id: str, asset_id: str, tenant: str = Depends(get_tenant),
              principal: str = Depends(require_identity)):
    """Serve an image/logo extracted during parsing (id without extension)."""
    found = store.load_asset(tenant, pair_id, asset_id)
    if found is None:
        raise HTTPException(status_code=404, detail="asset not found")
    data, content_type = found
    return Response(content=data, media_type=content_type)


# --- static UI (mounted last so it doesn't shadow the API routes) ------------
if _STATIC.exists():
    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
