"""
Acceso a Firestore: fuente de verdad para transcripciones, perfiles y capítulos.
Reemplaza la lectura de datos de pipeline desde sheets.py.
"""

import json
import os
import unicodedata

from google.cloud import firestore
from google.oauth2 import service_account

FAMILIA_ID = "marino-saraniti"
_SCOPES = ["https://www.googleapis.com/auth/datastore"]

_db_instance = None


def _get_db() -> firestore.Client:
    global _db_instance
    if _db_instance is not None:
        return _db_instance

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        _db_instance = firestore.Client(credentials=creds, project=info["project_id"])
    else:
        _db_instance = firestore.Client()  # ADC fallback

    return _db_instance


def _normalize(s: str) -> str:
    """Lowercase + strip accents for fuzzy name matching."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    ).strip()


def _find_integrante(nombre: str, familia_id: str = FAMILIA_ID):
    """Returns (data_dict, doc_ref) or (None, None). Tolerates accent/case differences."""
    db = _get_db()
    col = db.collection("familias").document(familia_id).collection("integrantes")

    # Try exact match first (fast)
    docs = list(col.where("nombre", "==", nombre).limit(1).stream())
    if docs:
        doc = docs[0]
        return doc.to_dict(), col.document(doc.id)

    # Fallback: client-side fuzzy match (accent + case insensitive)
    nombre_norm = _normalize(nombre)
    for doc in col.stream():
        d = doc.to_dict()
        if _normalize(d.get("nombre", "")) == nombre_norm:
            return d, col.document(doc.id)

    return None, None


# ─── Lectura de transcripciones ──────────────────────────────────────────────

def get_transcripciones(nombre: str, familia_id: str = FAMILIA_ID) -> list[dict]:
    """Returns [{pregunta, transcripcion}] en orden de pregunta."""
    _, ref = _find_integrante(nombre, familia_id)
    if not ref:
        return []

    result = []
    for rdoc in ref.collection("respuestas").stream():
        data = rdoc.to_dict()
        if not data.get("transcripcion"):
            continue
        result.append({
            "pregunta": data.get("pregunta", rdoc.id),
            "transcripcion": data["transcripcion"],
        })

    # Ordenar por pregunta si son numéricos
    def _sort_key(item):
        p = str(item["pregunta"])
        try:
            return int("".join(filter(str.isdigit, p)) or "0")
        except ValueError:
            return p

    return sorted(result, key=_sort_key)


# ─── Integrantes (para contexto familiar) ────────────────────────────────────

def get_familia_integrantes(familia_id: str = FAMILIA_ID) -> list[dict]:
    db = _get_db()
    docs = (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .stream()
    )
    result = []
    for doc in docs:
        d = doc.to_dict()
        nombre = d.get("nombre", "")
        if not nombre:
            continue
        fecha_fallec = d.get("fecha_fallec", "")
        result.append({
            "nombre": nombre,
            "fecha_nac": d.get("fecha_nac", ""),
            "fecha_fallec": fecha_fallec,
            "rol": d.get("relacion", d.get("rol", "")),
            "es_menor": d.get("es_menor", False),
            "vive": not bool(fecha_fallec),
        })
    return result


# ─── Foto URL (gs://) ────────────────────────────────────────────────────────

def get_foto_url(nombre: str, familia_id: str = FAMILIA_ID) -> str | None:
    data, _ = _find_integrante(nombre, familia_id)
    if not data:
        return None
    return data.get("foto_url") or None


# ─── Perfil de voz (voice_agent) ────────────────────────────────────────────

def save_profile(
    nombre: str,
    fecha_process: str,
    perfil_json: str,
    transcripcion_completa: str,
    familia_id: str = FAMILIA_ID,
):
    _, ref = _find_integrante(nombre, familia_id)
    if not ref:
        db = _get_db()
        ref = (
            db.collection("familias")
            .document(familia_id)
            .collection("integrantes")
            .document()
        )
        ref.set({"nombre": nombre}, merge=True)

    ref.set(
        {
            "perfil_voz": perfil_json,
            "transcripcion_completa": transcripcion_completa,
            "fecha_proceso": fecha_process,
        },
        merge=True,
    )


def get_profile(nombre: str, familia_id: str = FAMILIA_ID) -> dict | None:
    data, _ = _find_integrante(nombre, familia_id)
    if not data:
        return None

    perfil_str = data.get("perfil_voz", "")
    try:
        perfil_voz = json.loads(perfil_str) if perfil_str else {}
    except (json.JSONDecodeError, TypeError):
        perfil_voz = {}

    return {
        "nombre": nombre,
        "fecha_process": data.get("fecha_proceso", ""),
        "perfil_voz": perfil_voz,
        "transcripcion": data.get("transcripcion_completa", ""),
        "capitulo": data.get("capitulo", ""),
        "capitulo_revisado": data.get("capitulo_revisado", ""),
    }


# ─── Capítulo (chapter_agent) ────────────────────────────────────────────────

def save_chapter(
    nombre: str,
    capitulo: str,
    capitulo_revisado: str = "",
    familia_id: str = FAMILIA_ID,
):
    _, ref = _find_integrante(nombre, familia_id)
    if not ref:
        return
    update: dict = {"capitulo": capitulo}
    if capitulo_revisado:
        update["capitulo_revisado"] = capitulo_revisado
    ref.set(update, merge=True)
