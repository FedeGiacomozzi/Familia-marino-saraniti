"""
Cliente Firestore para el pipeline familiar.
Reemplaza la interface de sheets.py con persistencia en Firestore Native Mode.

Estructura de colecciones:
  familias/{familia_id}                         — documento de familia
  familias/{familia_id}/integrantes/{nombre_key}
  familias/{familia_id}/relaciones/{doc_id}
  familias/{familia_id}/respuestas/{doc_id}     — audios y transcripciones
  familias/{familia_id}/perfiles/{nombre_key}   — perfiles de voz y capítulos
  familias/{familia_id}/tokens/{token}          — tokens de acceso por integrante
"""

import json
import os
import secrets as _secrets
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


def _familia_ref(familia_id: str | None = None):
    fid = familia_id or FAMILIA_ID
    return _db().collection("familias").document(fid)


# ─── Familia ──────────────────────────────────────────────────────────────────

def create_familia(familia_id: str, nombre_familia: str, email_comprador: str) -> None:
    """Crea o actualiza el documento raíz de una familia."""
    _db().collection("familias").document(familia_id).set(
        {
            "nombre": nombre_familia,
            "comprador": {"email": email_comprador},
            "estado": "onboarding",
            "fecha_creacion": datetime.utcnow().isoformat(),
        },
        merge=True,
    )


def get_familia(familia_id: str) -> dict | None:
    """Retorna el documento raíz de una familia o None si no existe."""
    doc = _db().collection("familias").document(familia_id).get()
    if not doc.exists:
        return None
    return doc.to_dict() | {"_id": doc.id}


def update_familia_estado(familia_id: str, estado: str) -> None:
    """Actualiza solo el campo estado de una familia."""
    _db().collection("familias").document(familia_id).update({"estado": estado})


def update_familia_campo(familia_id: str, campo: str, valor) -> None:
    """Actualiza un campo arbitrario en el documento de familia."""
    _db().collection("familias").document(familia_id).update({campo: valor})


# ─── Tokens ───────────────────────────────────────────────────────────────────

def _generar_token(nombre: str) -> str:
    """Genera token con formato NOM-XXXXXX."""
    prefijo = nombre.strip()[:3].upper()
    sufijo = _secrets.token_urlsafe(6)[:6].upper()
    return f"{prefijo}-{sufijo}"


def create_token(familia_id: str, token: str, nombre: str, email: str) -> None:
    """Guarda un token en la subcolección tokens de la familia."""
    _db().collection("familias").document(familia_id).collection("tokens").document(token).set(
        {
            "token": token,
            "nombre": nombre,
            "email": email,
            "familia_id": familia_id,
            "usado": False,
            "fecha_creacion": datetime.utcnow().isoformat(),
        }
    )


def get_token(token: str) -> dict | None:
    """
    Busca un token en TODAS las familias usando collection group query.
    Retorna el documento o None si no existe.
    """
    docs = list(
        _db().collection_group("tokens").where("token", "==", token).limit(1).stream()
    )
    if not docs:
        return None
    return docs[0].to_dict() | {"_id": docs[0].id}


def marcar_token_usado(familia_id: str, token: str) -> None:
    """Marca un token como usado y registra la fecha de uso."""
    _db().collection("familias").document(familia_id).collection("tokens").document(token).update(
        {
            "usado": True,
            "fecha_uso": datetime.utcnow().isoformat(),
        }
    )


def get_tokens_familia(familia_id: str) -> list[dict]:
    """Retorna todos los tokens de una familia."""
    docs = (
        _db().collection("familias").document(familia_id).collection("tokens").stream()
    )
    return [d.to_dict() | {"_id": d.id} for d in docs]


# ─── Respuestas ───────────────────────────────────────────────────────────────

def get_respuestas(nombre: str, familia_id: str | None = None) -> list[dict]:
    """Devuelve lista de docs de respuesta para un nombre."""
    docs = (
        _familia_ref(familia_id)
        .collection("respuestas")
        .where("nombre_key", "==", _nombre_key(nombre))
        .stream()
    )
    return [d.to_dict() | {"_id": d.id} for d in docs]


def save_transcripcion(doc_id: str, transcripcion: str, familia_id: str | None = None):
    _familia_ref(familia_id).collection("respuestas").document(doc_id).update(
        {"transcripcion": transcripcion}
    )


def get_transcripciones(nombre: str, familia_id: str | None = None) -> list[dict]:
    """
    Retorna lista de {pregunta, transcripcion} con transcripcion no vacía.
    Compatible con la interface de sheets.get_transcripciones().
    """
    docs = get_respuestas(nombre, familia_id)
    result = []
    for d in docs:
        t = d.get("transcripcion", "").strip()
        if t:
            result.append({"pregunta": d.get("pregunta", ""), "transcripcion": t})
    return result


def get_fecha_nac(nombre: str, familia_id: str | None = None) -> str:
    docs = get_respuestas(nombre, familia_id)
    for d in docs:
        fn = d.get("fecha_nac", "").strip()
        if fn:
            return fn
    # Fallback: integrante
    integrante = get_integrante(nombre, familia_id)
    return integrante.get("fecha_nac", "") if integrante else ""


def get_foto_url(nombre: str, familia_id: str | None = None) -> str | None:
    docs = get_respuestas(nombre, familia_id)
    for d in docs:
        url = d.get("foto_url", "").strip()
        if url:
            return url
    return None


def get_all_nombres(familia_id: str | None = None) -> list[str]:
    docs = _familia_ref(familia_id).collection("respuestas").stream()
    nombres = set()
    for d in docs:
        n = d.to_dict().get("nombre", "").strip()
        if n:
            nombres.add(n)
    return sorted(nombres)


def get_all_respuestas(familia_id: str | None = None) -> list[dict]:
    """Todos los docs de respuestas de la familia (para transcriber)."""
    docs = _familia_ref(familia_id).collection("respuestas").stream()
    return [d.to_dict() | {"_id": d.id} for d in docs]


def get_respuestas_sin_transcripcion(nombre: str | None = None, familia_id: str | None = None) -> list[dict]:
    """Docs que tienen link_audio pero transcripcion vacía."""
    q = _familia_ref(familia_id).collection("respuestas")
    if nombre:
        q = q.where("nombre_key", "==", _nombre_key(nombre))
    return [
        d.to_dict() | {"_id": d.id}
        for d in q.stream()
        if d.to_dict().get("link_audio") and not d.to_dict().get("transcripcion")
    ]


# ─── Perfiles ─────────────────────────────────────────────────────────────────

def save_profile(nombre: str, fecha_process: str, perfil_json: str, transcripcion_completa: str, familia_id: str | None = None):
    key = _nombre_key(nombre)
    try:
        perfil_voz = json.loads(perfil_json) if isinstance(perfil_json, str) else perfil_json
    except (json.JSONDecodeError, ValueError):
        perfil_voz = {}
    _familia_ref(familia_id).collection("perfiles").document(key).set(
        {
            "nombre": nombre,
            "nombre_key": key,
            "fecha_process": fecha_process,
            "perfil_voz": perfil_voz,
            "transcripcion": transcripcion_completa,
        },
        merge=True,
    )


def save_chapter(nombre: str, capitulo: str, capitulo_revisado: str = "", familia_id: str | None = None):
    key = _nombre_key(nombre)
    data = {"capitulo": capitulo}
    if capitulo_revisado:
        data["capitulo_revisado"] = capitulo_revisado
    _familia_ref(familia_id).collection("perfiles").document(key).set(data, merge=True)


def get_profile(nombre: str, familia_id: str | None = None) -> dict | None:
    key = _nombre_key(nombre)
    doc = _familia_ref(familia_id).collection("perfiles").document(key).get()
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

def get_familia_integrantes(familia_id: str | None = None) -> list[dict]:
    docs = _familia_ref(familia_id).collection("integrantes").stream()
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
                "email": data.get("email", "").strip(),
                "pais": data.get("pais", "").strip(),
            }
        )
    return result


def get_familia_relaciones(familia_id: str | None = None) -> list[dict]:
    docs = _familia_ref(familia_id).collection("relaciones").stream()
    result = []
    for d in docs:
        data = d.to_dict()
        pa = data.get("persona_a", "").strip()
        rel = data.get("relacion", "").strip().lower()
        pb = data.get("persona_b", "").strip()
        if pa and rel and pb:
            result.append({"persona_a": pa, "relacion": rel, "persona_b": pb})
    return result


def get_integrante(nombre: str, familia_id: str | None = None) -> dict | None:
    integrantes = get_familia_integrantes(familia_id)
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
    email: str = "",
    pais: str = "",
    familia_id: str | None = None,
):
    """Upsert un integrante en Firestore."""
    key = _nombre_key(nombre)
    _familia_ref(familia_id).collection("integrantes").document(key).set(
        {
            "nombre": nombre,
            "nombre_key": key,
            "fecha_nac": fecha_nac,
            "fecha_fallec": fecha_fallec,
            "rol": rol.lower(),
            "es_menor": es_menor,
            "email": email,
            "pais": pais,
        },
        merge=True,
    )


def seed_relacion(persona_a: str, relacion: str, persona_b: str, familia_id: str | None = None):
    """Agrega una relación familiar (usa add para generar ID automático)."""
    _familia_ref(familia_id).collection("relaciones").add(
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
    familia_id: str | None = None,
) -> str:
    """Upsert una respuesta. Retorna el doc_id."""
    key = _nombre_key(nombre)
    doc_key = f"{key}__{str(pregunta).zfill(3)}"
    _familia_ref(familia_id).collection("respuestas").document(doc_key).set(
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


# ─── Helper para onboarding ──────────────────────────────────────────────────

def generar_token(nombre: str) -> str:
    """Genera token con formato NOM-XXXXXX (público para uso en endpoints)."""
    return _generar_token(nombre)
