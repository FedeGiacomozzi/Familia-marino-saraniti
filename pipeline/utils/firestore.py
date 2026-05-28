import json
import os
from datetime import datetime, timezone

from google.cloud import firestore
from google.oauth2 import service_account

_PROJECT_ID = "familia-marino"


def _get_creds():
    creds_json = os.environ.get("GCP_SA_KEY_JSON")
    if creds_json:
        info = json.loads(creds_json)
    else:
        path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "/secrets/credentials.json")
        with open(path) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/datastore"],
    )


def _client() -> firestore.Client:
    return firestore.Client(credentials=_get_creds(), project=_PROJECT_ID)


def _integrante_ref(db: firestore.Client, familia: str, nombre: str):
    return db.collection("familias").document(familia).collection("integrantes").document(nombre)


# ─── Transcripciones ──────────────────────────────────────────────────────────

def save_transcription(familia: str, nombre: str, texto: str):
    """Guarda o actualiza la transcripción completa de un integrante."""
    db = _client()
    ref = _integrante_ref(db, familia, nombre)
    ref.set(
        {"transcripcion": texto, "updated_at": datetime.now(timezone.utc)},
        merge=True,
    )


def get_transcripcion(familia: str, nombre: str) -> str | None:
    """Devuelve la transcripción de un integrante, o None si no existe."""
    db = _client()
    doc = _integrante_ref(db, familia, nombre).get()
    if doc.exists:
        return doc.to_dict().get("transcripcion")
    return None


# ─── Capítulos ────────────────────────────────────────────────────────────────

def save_chapter(familia: str, nombre: str, texto: str, revisado: bool = False):
    """
    Guarda el capítulo de un integrante.
    revisado=True escribe en el campo 'capitulo_revisado'.
    """
    db = _client()
    ref = _integrante_ref(db, familia, nombre)
    field = "capitulo_revisado" if revisado else "capitulo"
    ref.set(
        {field: texto, "updated_at": datetime.now(timezone.utc)},
        merge=True,
    )


def get_chapter(familia: str, nombre: str, revisado: bool = False) -> str | None:
    """Devuelve el capítulo (o capítulo revisado) de un integrante."""
    db = _client()
    doc = _integrante_ref(db, familia, nombre).get()
    if doc.exists:
        data = doc.to_dict()
        if revisado:
            return data.get("capitulo_revisado") or data.get("capitulo")
        return data.get("capitulo")
    return None


# ─── Integrantes ──────────────────────────────────────────────────────────────

def get_integrantes(familia: str) -> list[dict]:
    """
    Devuelve todos los integrantes de una familia.
    Cada dict incluye el campo 'nombre' (ID del documento).
    """
    db = _client()
    col = db.collection("familias").document(familia).collection("integrantes")
    docs = col.stream()
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["nombre"] = doc.id
        result.append(data)
    return result


def save_integrante(familia: str, datos: dict):
    """
    Crea o actualiza un integrante.
    datos debe incluir 'nombre' (usado como ID del documento).
    El resto de los campos se almacenan tal cual.
    """
    nombre = datos.get("nombre")
    if not nombre:
        raise ValueError("datos debe incluir el campo 'nombre'")
    db = _client()
    ref = _integrante_ref(db, familia, nombre)
    payload = {k: v for k, v in datos.items() if k != "nombre"}
    payload["updated_at"] = datetime.now(timezone.utc)
    ref.set(payload, merge=True)
