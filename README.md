# Generative Marketing Collateral

Turns two companies' context PDFs into a **tailored, factually-grounded B2B article**, emitted as
**structured JSON** that maps onto a layout template under hard constraints (word limits, image
placeholders, theme colours). ML6 Senior AI Engineer challenge, Case 2.

**Deployed on Google Cloud** — Cloud Run + GCS + Firestore + Pub/Sub + Vertex AI (Gemini 2.5).
The product is **two authenticated API endpoints**; the demo UI and an org dashboard are optional
thin clients over them. Full design → **[ARCHITECTURE.md](ARCHITECTURE.md)** · deploy → **[DEPLOY.md](DEPLOY.md)**.

Guiding idea: a **deterministic, typed pipeline**, not an open-ended agent — grounded in source-tagged
facts, with **layout limits enforced in code**, never left to the model.

```
parse → ground → draft (cited) → map to template → validate & repair → structured JSON (+ citations, confidence, cost)
```

---

## The two endpoints (the product)

| Method & path | Auth | What it does |
|---|---|---|
| `POST /companies/{pair_id}/documents` | Bearer | Upload sender/receiver PDFs. Parsed **async** (Pub/Sub → worker: Gemini multimodal for meaning + PyMuPDF for image/logo assets) into a typed, source-tagged `CompanyBrief`. Returns a `job_id`. |
| `POST /generate` | Bearer | `{pair_id, prompt, template_id}` → **`202` + `job_id`** (never blocks the request). Poll `GET /generate/{pair_id}/{job_id}` → `ArticleJSON` (+ citations, confidence, **token/cost**). An `Idempotency-Key` header makes retries return the same job. |

Supporting: `GET /templates` · `/companies/{pair_id}` · `/jobs/{pair_id}/{job_id}` · `/assets/{pair_id}/{id}` ·
`/healthz` · the UI at `/` · and `/internal/parse` (the Pub/Sub push worker). Machine-readable spec:
**`/openapi.json`** (Swagger at `/docs`) — the `HTTPBearer` scheme is attached to every protected route.

## Auth (from a terminal)
With `AUTH_MODE=google`, every data route needs a **Google ID token**. The deployed service is also
Cloud Run IAM-private, so one token satisfies both layers:
```bash
URL=https://<your-service>.run.app
TOKEN=$(gcloud auth print-identity-token --audiences="$URL")          # or impersonate an invoker SA
curl -H "Authorization: Bearer $TOKEN" "$URL/healthz"
```
Locally (`AUTH_MODE=none`, the default) no token is required. See [DEPLOY.md](DEPLOY.md) for the full flow.

---

## Run it

**Local (dev):**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                       # add '.[cloud]' for the GCS/Firestore/Pub-Sub backends
cp .env.example .env                   # GEMINI_API_KEY=…  (or the Vertex vars)
python sample_data/make_samples.py     # writes the two sample PDFs (with embedded images)
uvicorn app.main:app --reload          # → http://localhost:8000  (Collateral Studio UI)
```

**Cloud:** `./deploy.sh` (quick — Cloud Run + local store + an API key) **or** the full production stack
via Terraform in `infra/` (Cloud Run + GCS·CMEK + Firestore + Pub/Sub + least-privilege IAM + Vertex + auth, EU).

### Sample flow (curl)
```bash
curl -X POST "$URL/companies/demo1/documents" -F role=sender   -F files=@sample_data/sender_nimbus_ai.pdf       -H "Authorization: Bearer $TOKEN"
curl -X POST "$URL/companies/demo1/documents" -F role=receiver -F files=@sample_data/receiver_vanguard_freight.pdf -H "Authorization: Bearer $TOKEN"
# wait for both briefs (GET /companies/demo1), then:
curl -X POST "$URL/generate" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"pair_id":"demo1","prompt":"Show how Nimbus cuts Vanguard idle fleet hours"}'   # → 202 {job_id}
curl "$URL/generate/demo1/<job_id>" -H "Authorization: Bearer $TOKEN"                      # → {status, result}
```

### Tests
```bash
pip install '.[dev]' && pytest      # 27 tests, all offline (no network / no real model)
```
constraint engine · offline pipeline (injected fake LLM) · token/cost · async API (TestClient) · messaging.

---

## Design decisions (the tradeoffs)

- **PDF parsing — hybrid.** Gemini multimodal reads tables/charts/text into a typed brief (a VLM reads a
  table as *meaning*); PyMuPDF deterministically extracts logo/image bytes for the asset library. `Document AI`
  is the managed at-scale swap.
- **Grounding — long-context, not RAG (yet).** A bounded two-company corpus fits in context, so we stuff the
  *distilled, source-tagged* briefs — no vector DB, full traceability. Context assembly sits behind a
  `ContextAssembler` interface with a documented `RagAssembler` swap point.
- **Factual correctness — generate → verify.** The writer uses **only** brief facts, **abstains** rather than
  inventing, and tags each block with the fact-ids it used; a claim-level check flags unsupported/dangling
  citations. Grounding *reduces*, not *eliminates*, hallucination — so citations stay, and the JSON is a
  **reviewable draft** (the human review lives where the JSON is consumed; the API itself doesn't publish).
- **Structured output + hard limits.** Gemini controlled generation (`response_schema`) → schema-valid JSON,
  re-validated with Pydantic; word/char limits, placeholders and theme colours checked in the framework-free
  `constraints_core.py` and fixed by a **targeted repair loop** (capped, with sentence-boundary truncation as
  the graceful fallback).
- **Confidence + cost are computed, not self-reported.** `confidence` blends constraint compliance and
  faithfulness; every output carries token counts and **per-model USD cost**.
- **Prompt-injection posture.** PDFs are untrusted input: document text is a **separate DATA channel**, the
  system prompt forbids obeying instructions inside it, the writer sees only distilled facts, and a sanitise
  pass runs. `Model Armor` on Vertex sits in front in production.

---

## What's deployed on Google Cloud

| Concern | How |
|---|---|
| API host | **Cloud Run** (stateless, scale-to-zero) |
| Auth | **Google ID-token** verification (HTTPBearer) + Cloud Run IAM (defense in depth) |
| Model | **Vertex AI** — Gemini **2.5 Pro** (writer) / **2.5 Flash** (parse · verify · repair) |
| PDFs / assets | **Cloud Storage** (per-tenant prefix, CMEK) |
| Briefs / jobs / outputs | **Firestore** |
| Async parsing | **Pub/Sub** topic + push worker + dead-letter queue |
| Cost / observability | token + USD per generation; Cloud Logging/Trace |
| IaC | **Terraform** (`infra/`), EU region throughout |

*Roadmap:* Vertex AI Search (RAG when a corpus grows) · Agent Engine (if we add tools / a HITL pause) ·
org dashboard (Looker over BigQuery) · EU-region model residency (model calls currently use the `global` endpoint).

---

## Repo layout
```
app/
  main.py                 # FastAPI app, routes, auth + tenant deps, request-id middleware
  auth.py                 # Google ID-token verification (HTTPBearer, config-gated)
  config.py               # pydantic-settings — models, backends, knobs (all env-overridable)
  schemas.py              # Pydantic wire models + converters
  constraints_core.py     # framework-free constraint & repair engine (unit-tested)
  pricing.py              # per-model token → USD cost
  messaging.py            # Pub/Sub publish + push-envelope decode (async parse)
  templates_def/          # the layout templates (the hard contract)
  llm/gemini.py           # the only Gemini caller (both auth paths, retries, token usage)
  ingest/                 # parse_pdf.py (PyMuPDF, bytes) · brief_builder.py (multimodal → brief)
  generate/               # context.py · writer.py · layout.py (repair) · pipeline.py (orchestrator)
  eval/                   # constraint_checks.py · faithfulness.py (injectable judge)
  store/                  # base.py (interface) · local.py (fs) · cloud.py (Firestore + GCS) · get_store()
infra/                    # Terraform: Cloud Run, GCS·CMEK, Firestore, Pub/Sub, IAM, Vertex SA
static/                   # Collateral Studio UI (index.html, styles.css, app.js)
sample_data/              # make_samples.py + two image-rich sample PDFs
tests/                    # constraints · offline pipeline · usage/cost · async API · messaging
Dockerfile · deploy.sh · ARCHITECTURE.md · DEPLOY.md
```

---

## Status & honest limitations
**Deployed and tested end-to-end on GCP** (auth → upload → async parse → generate → grounded `ArticleJSON`
with cost). **Single-tenant** prototype, with the tenant plumbing already threaded for multi-tenant. Known
follow-ups: tune the faithfulness judge, pin the model to an EU region (currently `global`), and build the
org dashboard. The deterministic core is tested; generation quality depends on the live model + the briefs.

> Secrets live only in `.env` (git-ignored) / Secret Manager. Commit `.env.example`, never a real key.
