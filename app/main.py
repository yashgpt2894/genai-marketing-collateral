"""
FastAPI app — the two endpoints from the brief, plus supporting routes and the UI.

  POST /companies/{pair_id}/documents   upload sender/receiver PDFs -> parse -> briefs
  POST /generate                        {pair_id, prompt, template_id} -> ArticleJSON
                                        (synchronous by default; ?async=true -> 202 + poll URL)
  GET  /generate/{pair}/{job}           (optional) poll an async job / re-fetch a past result

  GET  /companies/{pair_id}  the stored briefs
  GET  /jobs/{pair_id}       list jobs (parse + generate)  ·  GET /jobs/{pair}/{job}  one job
  GET  /assets/{pair}/{id}   serve an extracted image  ·  DELETE /assets/{pair}/{id}  delete it
  GET  /health · /healthz    liveness + whether the LLM is configured
  GET  /                     the demo UI (static)

Design notes:
  * Storage is pluggable (LocalStore | CloudStore) via get_store(); all calls are
    tenant-scoped. tenant_id comes from the X-Tenant-ID header (default from config
    until real auth supplies it) — so the API is multi-tenant from the ground up.
  * Parsing can be slow, so uploads run in a BackgroundTask and return a job id
    (Pub/Sub + Cloud Run jobs in production).
  * /generate is synchronous by default — it returns the ArticleJSON in the response
    (the brief's contract). ?async=true runs it in the background and returns 202 +
    a poll URL, for very long or bursty jobs.
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
    Path as PathParam, Query, Request, Response, UploadFile,
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
from app.schemas import BriefsResponse, GenerateRequest, UploadResponse
from app.store import get_store
from app.templates_def.templates import get_template

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


@app.get("/healthz")  # k8s-style container probe (Cloud Run's edge shadows /healthz for browsers)
@app.get("/health")   # browser-reachable liveness, same payload
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
    blobs: list[tuple[str, bytes]] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=422, detail=f"only PDFs accepted, got '{f.filename}'")
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=(
                f"'{f.filename}' exceeds the {get_settings().max_upload_mb}MB upload limit"))
        blobs.append((f.filename or "upload.pdf", data))

    # Re-uploading a role fully REPLACES it: drop the prior PDF(s), extracted assets, and brief
    # for this (pair, role) first, so nothing from an older document can survive into a new
    # generation. (The other role is untouched; for a wholly new pairing use a fresh pair_id.)
    store.clear_role(tenant, pair_id, role)
    keys = [store.save_upload(tenant, pair_id, role, fn, data) for fn, data in blobs]

    job_id = uuid.uuid4().hex[:12]
    store.set_job(tenant, pair_id, job_id, "processing", f"parsing {len(keys)} file(s) for {role}", kind="parse")
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


@app.get("/jobs/{pair_id}")
def list_jobs(pair_id: str, tenant: str = Depends(get_tenant), principal: str = Depends(require_identity),
              job_type: str | None = Query(default=None, alias="type", description="filter: parse | generate"),
              status: str | None = Query(default=None, description="filter: processing | done | error"),
              limit: int = Query(default=50, ge=1, le=200)):
    """All jobs for a pair (parse + generate), newest first. Optional type/status filters + limit."""
    jobs = store.list_jobs(tenant, pair_id)
    if job_type:
        jobs = [j for j in jobs if j.get("kind") == job_type]
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    return {"pair_id": pair_id, "count": len(jobs), "jobs": jobs[:limit]}


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


def _run_and_store(tenant, pair_id, request_id, sender, receiver, prompt, template):
    """Draft -> verify -> repair (cache-checked), persist the article + job + metrics, and
    RETURN the ArticleJSON. Shared by the synchronous route and the async background task.
    Checks the result cache first (skips the model on a hit). Raises on failure — the caller
    decides whether to surface it as an HTTP error (sync) or record it on the job (async)."""
    cache = get_cache()
    ckey = gen_cache_key(tenant, pair_id, prompt, template.id, brief_fingerprint(sender, receiver))
    t0 = time.monotonic()

    cached = load_cached(cache, ckey)
    if cached is not None:
        cached.meta.request_id = request_id  # re-stamp so storage/poll keys match this job
        store.save_output(tenant, pair_id, cached)
        store.set_job(tenant, pair_id, request_id, "done", "article ready (cache hit)")
        emit_generation_metric(tenant, pair_id, cached,
                               latency_ms=int((time.monotonic() - t0) * 1000), cached=True)
        log.info("cache hit %s tenant=%s pair=%s", request_id, tenant, pair_id)
        return cached

    result = run_generation(pair_id, sender, receiver, prompt, template, request_id, llm=llm)
    store.save_output(tenant, pair_id, result)
    store.set_job(tenant, pair_id, request_id, "done", "article ready")
    cache.set(ckey, result.model_dump_json(), get_settings().cache_ttl_seconds)
    emit_generation_metric(tenant, pair_id, result,
                           latency_ms=int((time.monotonic() - t0) * 1000), cached=False)
    log.info("generated %s tenant=%s pair=%s (%d tok, $%.4f)",
             request_id, tenant, pair_id, result.meta.total_tokens, result.meta.cost_usd)
    return result


def _generate_job(tenant, pair_id, request_id, sender, receiver, prompt, template):
    """Async (?async=true) path: run the work off the request thread via a BackgroundTask,
    funnelling any failure into the job record so the poll endpoint can report it."""
    try:
        _run_and_store(tenant, pair_id, request_id, sender, receiver, prompt, template)
    except LLMNotConfigured as e:
        store.set_job(tenant, pair_id, request_id, "error", str(e))
    except LLMError as e:
        store.set_job(tenant, pair_id, request_id, "error", f"model error: {e}")
    except Exception as e:
        log.exception("generation failed")
        store.set_job(tenant, pair_id, request_id, "error", f"{type(e).__name__}: {e}")


@app.post("/generate")
def generate(req: GenerateRequest, background: BackgroundTasks,
             tenant: str = Depends(get_tenant), principal: str = Depends(require_identity),
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
             run_async: bool = Query(default=False, alias="async",
                 description="run in the background and return 202 + a poll URL instead of blocking")):
    """Generate the tailored article as structured JSON — the brief's second endpoint.

    Default (synchronous): runs the pipeline and returns the full ArticleJSON in the
    response body (200). This is the contract the brief asks for.

    `?async=true`: enqueue the work and return 202 + a poll URL
    (GET /generate/{pair_id}/{job_id}) — for very long or bursty jobs. Either way, send
    an `Idempotency-Key` header to make a retried POST reuse the same job/result instead
    of creating a duplicate."""
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

    # idempotency: a retried POST with the same key reuses the same job id (no duplicate work)
    if idempotency_key:
        request_id = "idem" + hashlib.sha1(
            f"{tenant}:{req.pair_id}:{idempotency_key}".encode()).hexdigest()[:12]
        existing = store.get_job(tenant, req.pair_id, request_id)
        if existing and existing.get("status") == "done":
            out = store.load_output(tenant, req.pair_id, request_id)
            if out is not None:                       # already generated under this key
                if run_async:
                    return {"pair_id": req.pair_id, "job_id": request_id, "status": "done",
                            "poll": f"/generate/{req.pair_id}/{request_id}", "idempotent": True}
                return JSONResponse(out.model_dump())  # sync: hand back the same article
        if existing and run_async:                    # async + still in flight -> same job handle
            return JSONResponse(status_code=202, content={
                "pair_id": req.pair_id, "job_id": request_id,
                "status": existing.get("status", "processing"),
                "poll": f"/generate/{req.pair_id}/{request_id}", "idempotent": True})
    else:
        request_id = uuid.uuid4().hex[:12]

    store.set_job(tenant, req.pair_id, request_id, "processing",
                  f"generating article (by {principal})", kind="generate")

    if run_async:                                     # opt-in: don't block, client polls the GET
        background.add_task(_generate_job, tenant, req.pair_id, request_id,
                            sender, receiver, req.prompt, template)
        return JSONResponse(status_code=202, content={
            "pair_id": req.pair_id, "job_id": request_id, "status": "processing",
            "poll": f"/generate/{req.pair_id}/{request_id}"})

    # default: synchronous — run inline and return the article (the brief's contract)
    try:
        result = _run_and_store(tenant, req.pair_id, request_id, sender, receiver, req.prompt, template)
    except LLMNotConfigured as e:
        store.set_job(tenant, req.pair_id, request_id, "error", str(e))
        raise HTTPException(status_code=503, detail=str(e))
    except LLMError as e:
        store.set_job(tenant, req.pair_id, request_id, "error", f"model error: {e}")
        raise HTTPException(status_code=502, detail=f"model error: {e}")
    except Exception as e:
        log.exception("generation failed")
        store.set_job(tenant, req.pair_id, request_id, "error", f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"generation failed: {type(e).__name__}: {e}")
    return JSONResponse(result.model_dump())


@app.get("/generate/{pair_id}/{job_id}")
def generate_status(pair_id: str, job_id: str, tenant: str = Depends(get_tenant),
                    principal: str = Depends(require_identity)):
    """Optional: poll an async (?async=true) job, or re-fetch a past generation by id.
    When done, returns the full ArticleJSON under `result`. The synchronous default path
    returns the article directly from POST /generate and never needs this."""
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


@app.delete("/assets/{pair_id}/{asset_id}")
def delete_asset(pair_id: str, asset_id: str, tenant: str = Depends(get_tenant),
                 principal: str = Depends(require_identity)):
    """Delete an extracted image/logo asset (id without extension)."""
    if not store.delete_asset(tenant, pair_id, asset_id):
        raise HTTPException(status_code=404, detail=f"asset '{asset_id}' not found for pair '{pair_id}'")
    log.info("deleted asset '%s' tenant=%s pair=%s (by %s)", asset_id, tenant, pair_id, principal)
    return {"deleted": asset_id}


# --- static UI (mounted last so it doesn't shadow the API routes) ------------
if _STATIC.exists():
    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
