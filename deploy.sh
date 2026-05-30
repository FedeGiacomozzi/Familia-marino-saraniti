#!/usr/bin/env bash
# Deploy the pipeline to Cloud Run.
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
SERVICE="familia-pipeline"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

echo "Project : ${PROJECT}"
echo "Region  : ${REGION}"
echo "Image   : ${IMAGE}"
echo ""

# Build and push via Cloud Build
gcloud builds submit \
  --project="${PROJECT}" \
  --tag="${IMAGE}" \
  .

# Deploy to Cloud Run
# --clear-env-vars evicts any plain env vars before setting secrets
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --timeout=900 \
  --service-account="familia-pipeline@${PROJECT}.iam.gserviceaccount.com" \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT},FAMILIA_ID=marino-saraniti,AUDIO_BUCKET=libro-familiar-audios,PDF_BUCKET=libro-familiar-pdfs,FOTO_BUCKET=libro-familiar-fotos,RESEND_FROM=Raíces <noreply@librofamiliar.com>,SERVICE_URL=https://familia-pipeline-776445604502.us-central1.run.app" \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GCP_SA_KEY_JSON=GOOGLE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest,MP_ACCESS_TOKEN=MP_ACCESS_TOKEN:latest,STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest,STRIPE_WEBHOOK_SECRET=STRIPE_WEBHOOK_SECRET:latest"

echo ""
echo "Deploy complete."
gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format="value(status.url)"
