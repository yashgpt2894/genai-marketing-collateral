# Generative Collateral Platform — Architecture

**The product is two authenticated API endpoints** — upload context PDFs; generate a
tailored, layout-valid article as structured JSON. The upload UI is an optional thin
client; the demo is from the terminal. Runs on **Google Cloud Run** with **Vertex AI**
for the model. **Single-tenant** prototype with a clean seam to multi-tenant.

---

## Diagram

```mermaid
flowchart TB
  CLIENT["curl / thin UI"]
  AUTH["Auth — Google ID token (Bearer), verified per request"]
  CLIENT --> AUTH
  AUTH --> API["CLOUD RUN · API (FastAPI, stateless)<br/>POST /companies/{pair}/documents<br/>POST /generate → 202 + job_id (poll)"]

  API -- "upload: PDF→GCS, publish job" --> PS["Pub/Sub"]
  PS --> WORK["Parse worker (Cloud Run, push)<br/>Document AI (+HITL) + Gemini 3.5 Flash"]
  WORK --> FS[("Firestore — briefs · jobs · outputs")]
  WORK --> GCS[("Cloud Storage — PDFs · assets · CMEK")]

  API -- "generate (async job)" --> GEN["Deterministic pipeline<br/>assemble→draft→map→validate→repair<br/>+ faithfulness + token/cost"]
  GEN -- "Vertex AI (service account, no key)" --> MODEL["Gemini 3.5 Pro / Flash"]
  GEN --> FS

  FS -. "metrics (roadmap)" .-> BQ[("BigQuery")]
  BQ -. .-> LOOK["Looker dashboard (roadmap)"]
```

Cross-cutting: **Model Armor** · per-tenant-ready **CMEK** · **EU residency** · **VPC-SC** ·
**Secret Manager** · least-privilege **IAM** (the Cloud Run SA has `roles/aiplatform.user`).

---

## Request flows
1. **Upload** `POST /companies/{pair}/documents` (auth) → PDF to GCS → **publish to Pub/Sub**
   (or in-process locally) → parse worker: **Document AI** (layout/tables/OCR, +HITL on
   low-confidence fields) + **Gemini 3.5 Flash** (meaning) → typed `CompanyBrief` in Firestore.
   Idempotent on `(role, filename)`; retries + dead-letter queue. Returns a `job_id`.
2. **Generate** `POST /generate {pair_id, prompt, template_id}` (auth) → **202 + `job_id`**,
   runs off the request path → grounded, cited draft → mapped to template → constraints
   validated **in code** → repair loop → `ArticleJSON` (+ citations, confidence, **token/cost**).
   Poll `GET /generate/{pair}/{job_id}`. An `Idempotency-Key` header de-dupes retries.

*(No publish/approval step — the API returns a reviewable draft; the human review happens
where the JSON is consumed. A formal approval gate enters only if we add a publish action.)*

---

## Components

| Plane | GCP service | Role |
|---|---|---|
| Auth | **Google ID token** (verified in-app) + Cloud Run IAM | every data route requires `Authorization: Bearer <id-token>` |
| API | **Cloud Run** (FastAPI) | the endpoints; stateless; scale-to-zero |
| Async parse | **Pub/Sub** + **Cloud Run** push worker | decoupled, idempotent, retried + DLQ |
| Parsing | **Document AI** (+HITL) + **Gemini 3.5 Flash** | layout/tables/OCR + multimodal meaning |
| Generation | deterministic pipeline on Cloud Run | assemble → draft → map → validate → repair |
| Model | **Vertex AI** — Gemini 3.5 Pro / Flash | via the Cloud Run service account (no API key) |
| Retrieval | long-context + (prod) context caching; structured facts in Firestore | bounded 2-company corpus |
| Storage | **Cloud Storage** (CMEK) · **Firestore** | PDFs/assets · briefs/jobs/outputs |
| Cost | per-generation token + USD on `meta` | per-model pricing |
| Governance | Model Armor · CMEK · VPC-SC · Secret Manager · IAM | security + responsible AI |
| CI/CD | **Cloud Build** + **Terraform** (`infra/`) | full stack as code, EU |

---

## API spec (the contract)

| Method · path | Auth | Body / params | Returns |
|---|---|---|---|
| `POST /companies/{pair}/documents` | Bearer | multipart: `role`, `files[]` (PDF) | `202` `{job_id}` |
| `GET  /jobs/{pair}/{job}` | Bearer | — | parse job status |
| `POST /generate` | Bearer | `{pair_id, prompt, template_id}` · `Idempotency-Key?` | `202` `{job_id, poll}` |
| `GET  /generate/{pair}/{job}` | Bearer | — | `{status, result: ArticleJSON}` |
| `GET  /companies/{pair}` · `GET /templates` · `GET /assets/{pair}/{id}` | Bearer | — | briefs · templates · image |
| `GET  /healthz` · `GET /` | public | — | liveness · UI |

Full machine-readable spec is the app's auto-generated **OpenAPI** at `/openapi.json` (`/docs`),
where the `HTTPBearer` security scheme is attached to every protected route.

---

## Locked decisions
- **Runtime = Cloud Run.** The pipeline is deterministic, stateless, tool-less → Cloud Run is
  the right host. **Agent Engine / ADK** is the documented upgrade *when we add tools, multi-step
  reasoning, memory, or a HITL pause* — it buys nothing today and costs more.
- **Auth = app-level Google ID-token verification** (`AUTH_MODE=google`), declared as an OpenAPI
  `HTTPBearer` scheme, paired with Cloud Run `--no-allow-unauthenticated` (defense in depth).
- **Retrieval = long-context + context caching**, not RAG; structured facts in Firestore;
  Vertex AI Search is the scale path. No separate vector DB.
- **Factual correctness** = generate-from-briefs + citations + per-claim faithfulness check.
- **Layout** = hard limits enforced **in code** (+ render-and-measure as the production truth).
- **Tenancy = single-tenant prototype** (`tenant` defaults to `default`); the store/routes are
  already tenant-keyed, so multi-tenant is a config + auth-claim change, not a refactor.
- **Models:** Gemini **3.5 Pro** (writer), **3.5 Flash** (parse / verify / repair).

---

## Roadmap (what's deliberately deferred)
- **Multi-tenancy:** derive `tenant_id` from the verified token claim (not a header); per-tenant
  CMEK + Firestore partition + rate/cost budgets. *(Plumbing already in place.)*
- **Org dashboard:** export per-generation metrics (confidence, faithfulness, cost) to BigQuery →
  Looker Studio, row-scoped by tenant. *(Backbone is a small `list_outputs` + `/sessions` add.)*
- **Agentic features → Agent Engine:** tools (live research, image sourcing), multi-step, HITL.
- **Render-and-measure** layout validation; richer eval (golden set, online auto-raters).

---

## Core vs additions
- **Core (the deliverable):** the two `curl`-able, authenticated endpoints.
- **Additions:** the upload UI and the org dashboard — thin clients over the API; not required.
