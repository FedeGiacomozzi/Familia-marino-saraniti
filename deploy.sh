#!/usr/bin/env bash
# Deploy the pipeline to Cloud Run.
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
SERVICE="familia-pipeline"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"
GCS_BUCKET="${GCS_BUCKET:-familia-marino-pdfs}"
SA="familia-pipeline@${PROJECT}.iam.gserviceaccount.com"

echo "Project : ${PROJECT}"
echo "Region  : ${REGION}"
echo "Image   : ${IMAGE}"
echo "Bucket  : ${GCS_BUCKET}"
echo ""

# ── GCS bucket para PDFs ────────────────────────────────────────────────────
if ! gsutil ls -b "gs://${GCS_BUCKET}" &>/dev/null; then
  echo "Creando bucket gs://${GCS_BUCKET}..."
  gsutil mb -p "${PROJECT}" -l "${REGION}" "gs://${GCS_BUCKET}"
  gsutil iam ch allUsers:objectViewer "gs://${GCS_BUCKET}"
  gsutil iam ch "serviceAccount:${SA}:objectAdmin" "gs://${GCS_BUCKET}"
  echo "Bucket creado y permisos configurados."
else
  echo "Bucket gs://${GCS_BUCKET} ya existe."
fi
echo ""

# Build and push via Cloud Build
gcloud builds submit \
  --project="${PROJECT}" \
  --tag="${IMAGE}" \
  .

# Sheet IDs (hardcoded in sheets.py, listed here for reference)
# SHEET_ID (Respuestas + Perfiles): 1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM
# FAMILIA_SHEET_ID (Integrantes + Relaciones): 1iEpnly_f3OQL6nLH41XU76zg1iM2vHZQyQdF0RLVQFE

# Deploy to Cloud Run (--clear-env-vars evicts any plain env vars before setting secrets)
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --timeout=3600 \
  --no-cpu-throttling \
  --service-account="familia-pipeline@${PROJECT}.iam.gserviceaccount.com" \
  --set-env-vars="GCS_BUCKET=${GCS_BUCKET},FONTS_DIR=/app/fonts,FIRESTORE_PROJECT_ID=${PROJECT}" \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GOOGLE_CREDENTIALS_JSON=GOOGLE_CREDENTIALS:latest"

echo ""
echo "Deploy complete."
gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format="value(status.url)"
