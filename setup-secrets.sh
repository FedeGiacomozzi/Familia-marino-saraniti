#!/usr/bin/env bash
# Script de configuración de secrets en GCP Secret Manager.
# Correr desde Cloud Shell ANTES del primer deploy.
# Requiere tener los valores a mano.

set -euo pipefail
PROJECT="${1:-familia-marino}"
SA="familia-pipeline@${PROJECT}.iam.gserviceaccount.com"

echo "=== Setup de secrets para proyecto: $PROJECT ==="
echo "Service Account: $SA"
echo ""

# Función helper: crea el secret si no existe, agrega permiso al SA
setup_secret() {
  local name=$1
  local desc=$2
  echo "--- $name ---"
  echo "Descripción: $desc"
  if ! gcloud secrets describe "$name" --project="$PROJECT" &>/dev/null; then
    gcloud secrets create "$name" \
      --project="$PROJECT" \
      --replication-policy="automatic"
    echo "Secret creado."
    echo ">>> Ingresá el valor (Enter para terminar, Ctrl+C para saltar):"
    read -r -s valor
    if [[ -n "$valor" ]]; then
      echo -n "$valor" | gcloud secrets versions add "$name" \
        --project="$PROJECT" \
        --data-file=-
      echo "Versión 1 guardada."
    else
      echo "Skipping — acordate de agregar el valor manualmente."
    fi
  else
    echo "Ya existe. Versiones disponibles:"
    gcloud secrets versions list "$name" --project="$PROJECT" --limit=3
  fi
  # IAM binding para que el SA pueda leerlo
  gcloud secrets add-iam-policy-binding "$name" \
    --project="$PROJECT" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet
  echo "IAM binding agregado para $SA"
  echo ""
}

# ── Secrets requeridos ──────────────────────────────────────────────────────

setup_secret "ANTHROPIC_API_KEY"    "API key de Anthropic (Claude) — sk-ant-..."
setup_secret "OPENAI_API_KEY"       "API key de OpenAI (Whisper) — sk-..."
setup_secret "GOOGLE_CREDENTIALS"   "JSON de Service Account de GCP (contenido del archivo .json)"
setup_secret "RESEND_API_KEY"       "API key de Resend — re_..."
setup_secret "MP_ACCESS_TOKEN"      "Access token de Mercado Pago (producción o sandbox) — APP_USR-..."
setup_secret "STRIPE_SECRET_KEY"    "Secret key de Stripe — sk_live_... o sk_test_..."
setup_secret "STRIPE_WEBHOOK_SECRET" "Webhook secret de Stripe — whsec_..."

echo "=== Setup completo ==="
echo ""
echo "Próximos pasos:"
echo "1. Correr ./deploy.sh $PROJECT"
echo "2. Verificar dominio en resend.com/domains"
echo "3. Registrar webhook en Mercado Pago → tu URL: https://familia-pipeline-776445604502.us-central1.run.app/pago/mp-webhook"
echo "4. Registrar webhook en Stripe Dashboard → URL: https://familia-pipeline-776445604502.us-central1.run.app/pago/stripe-webhook"
echo "   Eventos a escuchar: checkout.session.completed"
