#!/usr/bin/env bash
# Deploy the Generative Collateral API to Cloud Run — live, end-to-end.
#
# PERSONAL PROJECT ONLY. Use your PERSONAL Google account + a personal GCP
# project (the free $300 credits are plenty). Do NOT use an Infosys / Liberty
# Global account, project, or credentials. The sample data is fictional
# (Nimbus AI / Vanguard Freight) — nothing confidential is involved.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID=your-personal-gcp-project}"
REGION="${REGION:-europe-west1}"     # EU region by default
SERVICE="${SERVICE:-collateral-api}"

echo "Project: $PROJECT_ID   Region: $REGION   Service: $SERVICE"
gcloud config set project "$PROJECT_ID"

# One-time: enable the APIs the deploy needs.
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

# One-time: store your Gemini (AI Studio) API key as a secret — never in code/env files:
#   printf %s "YOUR_GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=-
# Rotate later with:
#   printf %s "NEW_KEY" | gcloud secrets versions add gemini-api-key --data-file=-

# Build from source (Cloud Build uses the Dockerfile) and deploy.
#   --allow-unauthenticated  -> plain `curl` works (no token). Drop it for the
#                               Bearer-token / IAM-authed variant (see DEPLOY.md).
#   --max-instances 1        -> the prototype's LocalStore is per-instance, so
#                               upload + generate must hit the same instance.
#                               (Swap to GCS + Firestore to remove this limit.)
#   --timeout 600            -> POST /generate is synchronous (it returns the article),
#                               so the request timeout must exceed worst-case generation.
#                               10 min is generous headroom; use ?async=true for longer jobs.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest" \
  --set-env-vars "MODEL_WRITER=gemini-3.5-flash,MODEL_PARSER=gemini-3.5-flash,MODEL_CHEAP=gemini-3.5-flash,GOOGLE_CLOUD_LOCATION=$REGION" \
  --memory 1Gi --cpu 1 --timeout 600 --max-instances 1

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "Live at: $URL"
echo "Health : curl -s $URL/healthz"
