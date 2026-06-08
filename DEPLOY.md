# Deploy to Cloud Run (live, end-to-end)

> **Personal project.** Deploy to your **personal** Google Cloud account (personal
> email + the free $300 credits) — **never** an Infosys / Liberty Global account or
> project. Sample data is fictional; nothing confidential leaves your machine except
> the app code going to *your own* cloud project when *you* run the deploy.

The app is already Cloud Run-shaped (a stateless FastAPI service), so going live is
three steps. No local Docker needed — Cloud Build builds the container for you.

## Prerequisites
- A personal GCP project with billing (free credits are fine).
- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authed: `gcloud auth login`.
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

## Deploy
```bash
cd case2-genai-collateral
gcloud auth login                                   # your personal account
export PROJECT_ID=your-personal-project

# store the key as a secret (not in code/env):
printf %s "YOUR_GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=- --project "$PROJECT_ID"

./deploy.sh                                          # builds + deploys, prints the URL
```

## Test the two endpoints with curl (the core product)
The UI and the org dashboard are *additions* — the product is these two endpoints,
and they work with plain `curl`:

```bash
URL=https://collateral-api-XXXX.europe-west1.run.app   # printed by deploy.sh

# 1) upload sender + receiver PDFs  (multipart: fields are `role` and `files`)
curl -X POST "$URL/companies/demo1/documents" -F "role=sender"   -F "files=@sample_data/sender_nimbus_ai.pdf"
curl -X POST "$URL/companies/demo1/documents" -F "role=receiver" -F "files=@sample_data/receiver_vanguard_freight.pdf"

# 2) generate the tailored article as structured JSON
curl -X POST "$URL/generate" -H "Content-Type: application/json" -d '{
  "pair_id": "demo1",
  "prompt": "Show how Nimbus AI cuts Vanguard'\''s idle fleet hours",
  "template_id": "one_pager_v1"
}'
```

### Authenticated variant (production)
Drop `--allow-unauthenticated` from `deploy.sh`, then send an identity token —
this is where your `Authorization: Bearer …` header comes in:
```bash
curl -X POST "$URL/generate" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" -d '{ ... }'
```

## Notes
- **Model IDs:** the quick deploy defaults all three to the GA `gemini-2.5-flash`. If a
  model id isn't enabled in your project/region yet, set `MODEL_*` to one that is
  (e.g. `gemini-2.5-pro` for the writer).
- **Persistence:** the prototype stores briefs/outputs on the instance's local disk,
  so the deploy pins `--max-instances 1` (upload + generate hit the same instance).
  Swapping `LocalStore` for GCS + Firestore removes that limit — that's the
  production storage layer described in the README.
- **Teardown:** `gcloud run services delete collateral-api --region europe-west1`.
