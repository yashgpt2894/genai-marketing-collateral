# Infrastructure (Terraform) — full production stack

> **Personal GCP project only** (personal account + free credits) — never an
> Infosys / Liberty Global project. Everything is pinned to an **EU region**.

Provisions the production architecture:

- **Cloud Run** — the API, wired to the **Vertex AI** LLM path (no API key; the
  service account has `roles/aiplatform.user`), `STORE_BACKEND=cloud`, `PARSE_BACKEND=pubsub`.
- **GCS** — PDFs + assets, **CMEK** (customer-managed key), EU, 365-day retention.
- **Firestore** — briefs / jobs / outputs.
- **Pub/Sub** — `collateral-parse` topic + **push** subscription to `/internal/parse`
  (OIDC-authed) + **dead-letter** topic, retries.
- **IAM** — a least-privilege Cloud Run service account (Firestore, the bucket only,
  Vertex, publish) + a separate push SA that can invoke Cloud Run.
- **Artifact Registry** — the container image repo.

This is the **full-stack** deploy. `../deploy.sh` is the *quick* path (Cloud Run +
local store + an API key) for a fast smoke test; this Terraform is the real thing.

## Apply (two phases — the image must exist before Cloud Run can reference it)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set project_id

terraform init

# Phase 1 — create the APIs + Artifact Registry repo
terraform apply -target=google_project_service.enabled \
                -target=google_artifact_registry_repository.repo

# Build + push the image (Cloud Build) into that repo
REPO="$(terraform output -raw artifact_registry)"
gcloud builds submit .. --tag "$REPO/api:v1"      # builds ../Dockerfile

# Phase 2 — full apply with the image
terraform apply -var "image=$REPO/api:v1"
```

## Test it end-to-end
```bash
URL="$(terraform output -raw service_url)"
curl -s "$URL/healthz"        # -> {"store":"cloud", llm_configured:true (Vertex), ...}

curl -X POST "$URL/companies/demo1/documents" -F role=sender   -F files=@../sample_data/sender_nimbus_ai.pdf
curl -X POST "$URL/companies/demo1/documents" -F role=receiver -F files=@../sample_data/receiver_vanguard_freight.pdf
curl -X POST "$URL/generate" -H 'Content-Type: application/json' \
  -d '{"pair_id":"demo1","prompt":"show how Nimbus cuts Vanguard idle hours"}'   # -> 202 + job_id
# poll the returned job_id:
# curl "$URL/generate/demo1/<job_id>"
```

## Teardown
```bash
terraform destroy
```

## Notes
- The model ids default to `gemini-2.5-pro` / `gemini-2.5-flash`; override with
  `-var model_writer=` / `-var model_flash=` if those aren't enabled in your project/region.
- Firestore allows one `(default)` database per project; if you already have one,
  remove `google_firestore_database.db` from the plan.
- Multi-tenancy is by `X-Tenant-ID` (an API-gateway / Identity Platform concern in
  front of Cloud Run); the data isolation (per-tenant keys/paths) is already in the app.
