"""
Cliente Firestore para el pipeline familiar.
Reemplaza la interface de sheets.py con persistencia en Firestore Native Mode.

Estructura de colecciones:
  familias/{familia_id}/respuestas/{doc_id}   — audios y transcripciones
  familias/{familia_id}/perfiles/{nombre_key} — perfiles de voz y capítulos
  familias/{familia_id}/integrantes/{nombre_key}
  familias/{familia_id}/relaciones/{doc_id}
"""

import json
import os
from datetime import datetime
from functools import lru_cache

from google.cloud import firestore
from google.oauth2 import service_account

FAMILIA_ID = os.environ.get("FAMILIA_ID", "marino-saraniti")
_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "familia-marino")


def _nombre_key(nombre: str) -> str:
    return nombre.strip().lower().replace(" ", "_")


@lru_cache(maxsize=1)
def _db() -> firestore.Client:
    cred_raw = os.environ.get("GCP_SA_KEY_JSON")
    if cred_raw:
        try:
            info = json.loads(cred_raw)
        except (json.JSONDecodeError, ValueError):
            with open(cred_raw) as f:
                info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/datastore"],
        )
        return firestore.Client(project=_PROJECT_ID, credentials=creds)
    # En Cloud Run con ADC o Workload Identity
    return firestore.Client(project=_PROJECT_ID)


def _familia_ref():
    return _db().collection("familias").document(FAMILIA_ID)


# ─── Respuestas ───────────────────────────────────────────────────────────────

def get_respuestas(nombre: str) -> list[dict]:
    """Devuelve lista de docs de respuesta para un nombre."""
    docs = (
        _familia_ref()
        .collection("respuestas")
        .where("nombre_key", "==", _nombre_key(nombre))
        .stream()
    )
    return [d.to_dict() | {"_id": d.id} for d in docs]


def save_transcripcion(doc_id: str, transcripcion: str):
    _familia_ref().collection("respuestas").document(doc_id).update(
        {"transcripcion": transcripcion}
    )


def get_transcripciones(nombre: str) -> list[dict]:
    """
    Retorna lista de {pregunta, transcripcion} con transcripcion no vacía.
    Compatible con la interface de sheets.get_transcripciones().
    """
    docs = get_respuestas(nombre)
    result = []
    for d in docs:
        t = d.get("transcripcion", "").strip()
        if t:
            result.append({"pregunta": d.get("pregunta", ""), "transcripcion": t})
    return result


def get_fecha_nac(nombre: str) -> str:
    docs = get_respuestas(nombre)
    for d in docs:
        fn = d.get("fecha_nac", "").strip()
        if fn:
            return fn
    # Fallback: integrante
    integrante = get_integrante(nombre)
    return integrante.get("fecha_nac", "") if integrante else ""


def get_foto_url(nombre: str) -> str | None:
    docs = get_respuestas(nombre)
    for d in docs:
        url = d.get("foto_url", "").strip()
        if url:
            return url
    return None


def get_all_nombres() -> list[str]:
    docs = _familia_ref().collection("respuestas").stream()
    nombres = set()
    for d in docs:
        n = d.to_dict().get("nombre", "").strip()
        if n:
            nombres.add(n)
    return sorted(nombres)


def get_all_respuestas() -> list[dict]:
    """Todos los docs de respuestas de la familia (para transcriber)."""
    docs = _familia_ref().collection("respuestas").stream()
    return [d.to_dict() | {"_id": d.id} for d in docs]


def get_respuestas_sin_transcripcion(nombre: str | None = None) -> list[dict]:
    """Docs que tienen link_audio pero transcripcion vacía."""
    q = _familia_ref().collection("respuestas")
    if nombre:
        q = q.where("nombre_key", "==", _nombre_key(nombre))
    return [
        d.to_dict() | {"_id": d.id}
        for d in q.stream()
        if d.to_dict().get("link_audio") and not d.to_dict().get("transcripcion")
    ]


# ─── Perfiles ─────────────────────────────────────────────────────────────────

def save_profile(nombre: str, fecha_process: str, perfil_json: str, transcripcion_completa: str):
    key = _nombre_key(nombre)
    try:
        perfil_voz = json.loads(perfil_json) if isinstance(perfil_json, str) else perfil_json
    except (json.JSONDecodeError, ValueError):
        perfil_voz = {}
    _familia_ref().collection("perfiles").document(key).set(
        {
            "nombre": nombre,
            "nombre_key": key,
            "fecha_process": fecha_process,
            "perfil_voz": perfil_voz,
            "transcripcion": transcripcion_completa,
        },
        merge=True,
    )


def save_chapter(nombre: str, capitulo: str, capitulo_revisado: str = ""):
    key = _nombre_key(nombre)
    data = {"capitulo": capitulo}
    if capitulo_revisado:
        data["capitulo_revisado"] = capitulo_revisado
    _familia_ref().collection("perfiles").document(key).set(data, merge=True)


def get_profile(nombre: str) -> dict | None:
    key = _nombre_key(nombre)
    doc = _familia_ref().collection("perfiles").document(key).get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    return {
        "nombre": d.get("nombre", nombre),
        "fecha_process": d.get("fecha_process", ""),
        "perfil_voz": d.get("perfil_voz", {}),
        "transcripcion": d.get("transcripcion", ""),
        "capitulo": d.get("capitulo", ""),
        "capitulo_revisado": d.get("capitulo_revisado", ""),
    }


# ─── Familia: Integrantes + Relaciones ───────────────────────────────────────

def get_familia_integrantes() -> list[dict]:
    docs = _familia_ref().collection("integrantes").stream()
    result = []
    for d in docs:
        data = d.to_dict()
        nombre = data.get("nombre", "").strip()
        if not nombre:
            continue
        fecha_fallec = data.get("fecha_fallec", "").strip()
        result.append(
            {
                "nombre": nombre,
                "fecha_nac": data.get("fecha_nac", "").strip(),
                "fecha_fallec": fecha_fallec,
                "rol": data.get("rol", "").strip().lower(),
                "es_menor": bool(data.get("es_menor", False)),
                "vive": not bool(fecha_fallec),
            }
        )
    return result


def get_familia_relaciones() -> list[dict]:
    docs = _familia_ref().collection("relaciones").stream()
    result = []
    for d in docs:
        data = d.to_dict()
        pa = data.get("persona_a", "").strip()
        rel = data.get("relacion", "").strip().lower()
        pb = data.get("persona_b", "").strip()
        if pa and rel and pb:
            result.append({"persona_a": pa, "relacion": rel, "persona_b": pb})
    return result


def get_integrante(nombre: str) -> dict | None:
    integrantes = get_familia_integrantes()
    nombre_lower = nombre.strip().lower()
    for p in integrantes:
        if p["nombre"].lower() == nombre_lower:
            return p
    return None


# ─── Pure helpers (sin IO) ───────────────────────────────────────────────────

def build_family_context(nombre: str, integrantes: list[dict], relaciones: list[dict]) -> dict:
    """Igual que sheets.build_family_context — función pura."""
    nombre_lower = nombre.strip().lower()
    conyuges, hijos_de, padres_de = [], [], []
    for r in relaciones:
        a, rel, b = r["persona_a"].lower(), r["relacion"], r["persona_b"].lower()
        if rel in ("cónyuge", "conyuge"):
            if a == nombre_lower:
                conyuges.append(r["persona_b"])
            elif b == nombre_lower:
                conyuges.append(r["persona_a"])
        elif rel in ("padre", "madre"):
            if a == nombre_lower:
                hijos_de.append(r["persona_b"])
            elif b == nombre_lower:
                padres_de.append(r["persona_a"])

    mis_padres = {
        r["persona_a"].lower()
        for r in relaciones
        if r["persona_b"].lower() == nombre_lower and r["relacion"] in ("padre", "madre")
    }
    siblings = {
        r["persona_b"]
        for r in relaciones
        if r["relacion"] in ("padre", "madre")
        and r["persona_a"].lower() in mis_padres
        and r["persona_b"].lower() != nombre_lower
    }

    integrante = get_integrante(nombre) or {}
    return {
        "rol": integrante.get("rol", ""),
        "vive": integrante.get("vive", True),
        "fecha_fallec": integrante.get("fecha_fallec", ""),
        "es_menor": integrante.get("es_menor", False),
        "conyuges": conyuges,
        "hijos": hijos_de,
        "padres": padres_de,
        "hermanos": sorted(siblings),
    }


def get_fallecidos(integrantes: list[dict]) -> list[dict]:
    return [p for p in integrantes if not p["vive"]]


# ─── Seed helpers (carga inicial desde Sheets) ───────────────────────────────

def seed_integrante(
    nombre: str,
    fecha_nac: str = "",
    fecha_fallec: str = "",
    rol: str = "",
    es_menor: bool = False,
):
    """Upsert un integrante en Firestore."""
    key = _nombre_key(nombre)
    _familia_ref().collection("integrantes").document(key).set(
        {
            "nombre": nombre,
            "nombre_key": key,
            "fecha_nac": fecha_nac,
            "fecha_fallec": fecha_fallec,
            "rol": rol.lower(),
            "es_menor": es_menor,
        },
        merge=True,
    )


def seed_relacion(persona_a: str, relacion: str, persona_b: str):
    """Agrega una relación familiar (usa add para generar ID automático)."""
    _familia_ref().collection("relaciones").add(
        {
            "persona_a": persona_a,
            "relacion": relacion.lower(),
            "persona_b": persona_b,
        }
    )


def seed_respuesta(
    nombre: str,
    pregunta: str,
    link_audio: str,
    foto_url: str = "",
    fecha_nac: str = "",
    transcripcion: str = "",
) -> str:
    """Upsert una respuesta. Retorna el doc_id."""
    key = _nombre_key(nombre)
    doc_key = f"{key}__{str(pregunta).zfill(3)}"
    _familia_ref().collection("respuestas").document(doc_key).set(
        {
            "nombre": nombre,
            "nombre_key": key,
            "pregunta": str(pregunta),
            "link_audio": link_audio,
            "foto_url": foto_url,
            "fecha_nac": fecha_nac,
            "transcripcion": transcripcion,
            "fecha_hora": datetime.utcnow().isoformat(),
        },
        merge=True,
    )
    return doc_key
