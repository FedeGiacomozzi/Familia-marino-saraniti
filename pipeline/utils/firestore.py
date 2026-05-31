"""
Firestore client para el pipeline familia-marino.

Colecciones esperadas (GCP project: familia-marino):
  respuestas   — una doc por grabación: {nombre, pregunta, audio_uri, transcripcion, ...}
  integrantes  — una doc por persona
  relaciones   — una doc por relación

El cliente se inicializa con ADC (Application Default Credentials) en Cloud Run,
o con GOOGLE_CREDENTIALS_JSON como env var para desarrollo local.
"""

import json
import os
from typing import Optional

from google.cloud import firestore
from google.oauth2 import service_account

_DB: Optional[firestore.Client] = None


def _db() -> firestore.Client:
    global _DB
    if _DB is None:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            info = json.loads(creds_json)
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/datastore"],
            )
            _DB = firestore.Client(project=info.get("project_id"), credentials=creds)
        else:
            # En Cloud Run usa ADC automáticamente
            _DB = firestore.Client()
    return _DB


# ── Respuestas ────────────────────────────────────────────────────────────────

def get_respuestas_sin_transcribir() -> list[dict]:
    """
    Devuelve las docs de la colección 'respuestas' que tienen audio_uri
    pero no tienen transcripcion (o está vacía).
    """
    db = _db()
    docs = db.collection("respuestas").stream()
    pendientes = []
    for doc in docs:
        d = doc.to_dict()
        d["_id"] = doc.id
        audio_uri = d.get("audio_uri") or d.get("audio_gcs_uri") or d.get("link_audio", "")
        transcripcion = d.get("transcripcion", "").strip()
        if audio_uri and not transcripcion:
            d["audio_uri"] = audio_uri
            pendientes.append(d)
    return pendientes


def get_todas_las_respuestas() -> list[dict]:
    """Devuelve todas las respuestas, con o sin transcripción."""
    db = _db()
    docs = db.collection("respuestas").stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["_id"] = doc.id
        result.append(d)
    return result


def save_transcripcion(doc_id: str, transcripcion: str) -> None:
    """Guarda la transcripción en el doc de respuestas indicado."""
    _db().collection("respuestas").document(doc_id).update({
        "transcripcion": transcripcion,
    })


def get_respuestas_por_nombre(nombre: str) -> list[dict]:
    db = _db()
    docs = (
        db.collection("respuestas")
        .where("nombre", "==", nombre)
        .stream()
    )
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["_id"] = doc.id
        result.append(d)
    return result


# ── Integrantes ───────────────────────────────────────────────────────────────

def get_integrantes() -> list[dict]:
    db = _db()
    docs = db.collection("integrantes").stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["_id"] = doc.id
        result.append(d)
    return result


# ── Relaciones ────────────────────────────────────────────────────────────────

def get_relaciones() -> list[dict]:
    db = _db()
    docs = db.collection("relaciones").stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["_id"] = doc.id
        result.append(d)
    return result
