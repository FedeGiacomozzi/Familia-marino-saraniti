#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "==> Installing Python dependencies..."
pip install --quiet -r "$CLAUDE_PROJECT_DIR/pipeline/requirements.txt"

# GCP audit libraries (additional to project requirements)
pip install --quiet \
  google-cloud-storage \
  google-cloud-firestore \
  google-cloud-run

echo "==> Configuring GCP credentials..."
if [ -n "${GCP_SA_KEY_JSON:-}" ]; then
  echo "$GCP_SA_KEY_JSON" > /tmp/sa_key.json
  chmod 600 /tmp/sa_key.json
  echo "export GOOGLE_APPLICATION_CREDENTIALS=/tmp/sa_key.json" >> "$CLAUDE_ENV_FILE"
  echo "    GCP credentials written from GCP_SA_KEY_JSON"
else
  echo "    WARNING: GCP_SA_KEY_JSON not set — GCP authentication not configured"
  echo "    Set this env var in your Claude Code environment settings"
fi

echo "==> Session start complete"
