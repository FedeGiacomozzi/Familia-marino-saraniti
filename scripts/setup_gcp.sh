#!/usr/bin/env bash
# setup_gcp.sh — Crea buckets GCS y permisos para el pipeline familiar.
# Ejecutar UNA sola vez desde Cloud Shell con permisos de owner.
#
# Usage: bash scripts/setup_gcp.sh [PROJECT_ID]

set -euo pipefail

PROJECT="${1:-familia-marino}"
REGION="southamerica-east1"
SA="familia-pipeline@${PROJECT}.iam.gserviceaccount.com"

BUCKETS=(
  "libro-familiar-audios"
  "libro-familiar-pdfs"
  "libro-familiar-fotos"
)

echo "=== Proyecto  : ${PROJECT}"
echo "=== Región    : ${REGION}"
echo "=== SA        : ${SA}"
echo ""

# ── Crear buckets ─────────────────────────────────────────────────────────────
for BUCKET in "${BUCKETS[@]}"; do
  if gsutil ls -b "gs://${BUCKET}" &>/dev/null; then
    echo "[skip] gs://${BUCKET} ya existe"
  else
    echo "[create] gs://${BUCKET}"
    gsutil mb \
      -p "${PROJECT}" \
      -l "${REGION}" \
      -b on \
      "gs://${BUCKET}"
  fi
done

echo ""

# ── Permisos a la Service Account ─────────────────────────────────────────────
echo "=== Asignando permisos GCS a ${SA}..."

for BUCKET in "${BUCKETS[@]}"; do
  gsutil iam ch \
    "serviceAccount:${SA}:roles/storage.objectAdmin" \
    "gs://${BUCKET}"
  echo "  ✓ objectAdmin → gs://${BUCKET}"
done

# ── Firestore ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Asignando roles Firestore a ${SA}..."

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role="roles/datastore.user" \
  --condition=None \
  --quiet

echo "  ✓ roles/datastore.user"

# ── Habilitar APIs necesarias ─────────────────────────────────────────────────
echo ""
echo "=== Habilitando APIs..."
gcloud services enable \
  firestore.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT}" \
  --quiet

echo ""
echo "✅ Setup completo."
echo ""
echo "Verificá Firestore Native Mode en:"
echo "  https://console.cloud.google.com/firestore/data?project=${PROJECT}"
echo ""
echo "Si Firestore aún está en Datastore mode, activá Native mode desde esa URL"
echo "ANTES de correr el seed script."
