#!/bin/bash
set -e

SERVICE_NAME=${1:-familia-marino}
PROJECT_ID="familia-marino"
REGION="us-central1"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "Building and deploying $SERVICE_NAME to Cloud Run..."

gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID"

gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --platform managed \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --service-account "familia-pipeline@familia-marino.iam.gserviceaccount.com" \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GOOGLE_CREDENTIALS_JSON=GOOGLE_CREDENTIALS:latest"

echo "Deploy complete!"
gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --format="value(status.url)"
