"""
Firestore client — job tracking and recording token management.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore
from google.oauth2 import service_account

from pipeline.utils.secrets import get_secret

_db: Optional[firestore.Client] = None


def _get_db() -> firestore.Client:
    global _db
    if _db is None:
        creds_json = get_secret("GOOGLE_CREDENTIALS_JSON")
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/datastore"],
        )
        _db = firestore.Client(project=creds_dict["project_id"], credentials=creds)
    return _db


# ── Jobs ──────────────────────────────────────────────────────────────────────

def create_job(job_id: str, data: dict) -> None:
    _get_db().collection("jobs").document(job_id).set({
        **data,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def update_job(job_id: str, data: dict) -> None:
    _get_db().collection("jobs").document(job_id).update(data)


def get_job(job_id: str) -> Optional[dict]:
    doc = _get_db().collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


# ── Familias / tokens ─────────────────────────────────────────────────────────

def get_familia(familia_id: str) -> Optional[dict]:
    doc = _get_db().collection("familias").document(familia_id).get()
    return doc.to_dict() if doc.exists else None


def get_tokens_familia(familia_id: str) -> list[dict]:
    tokens = (
        _get_db()
        .collection("familias")
        .document(familia_id)
        .collection("tokens")
        .stream()
    )
    return [{"id": t.id, **t.to_dict()} for t in tokens]


def mark_token_completado(familia_id: str, token_id: str, audio_gcs_url: str) -> None:
    (
        _get_db()
        .collection("familias")
        .document(familia_id)
        .collection("tokens")
        .document(token_id)
        .update({
            "completado": True,
            "audio_url": audio_gcs_url,
            "completado_at": datetime.now(timezone.utc).isoformat(),
        })
    )
