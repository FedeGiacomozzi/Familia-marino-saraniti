#!/bin/bash
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:-familia-marino}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="familia-pipeline"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT="$2"; shift 2 ;;
    --region)  REGION="$2";  shift 2 ;;
    *) echo "Argumento desconocido: $1"; exit 1 ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Proyecto : $PROJECT"
echo " Región   : $REGION"
echo " Servicio : $SERVICE"
echo " Imagen   : $IMAGE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "[1/3] Building Docker image..."
cd pipeline
gcloud builds submit \
  --project="$PROJECT" \
  --tag="$IMAGE" \
  .

echo "[2/3] Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --image="$IMAGE" \
  --platform=managed \
  --no-allow-unauthenticated \
  --service-account="familia-pipeline@${PROJECT}.iam.gserviceaccount.com" \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT}" \
  --memory=1Gi \
  --cpu=1 \
  --timeout=540 \
  --max-instances=3

echo "[3/3] Obteniendo URL del servicio..."
SERVICE_URL=$(gcloud run services describe "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --format="value(status.url)")

echo ""
echo "Deploy exitoso"
echo "  URL: $SERVICE_URL"
echo ""
echo "Probá el health check:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${SERVICE_URL}/health"
