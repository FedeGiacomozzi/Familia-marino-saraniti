import os
import json
import uuid as _uuid

from google.cloud import firestore

FIRESTORE_PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "familia-marino")

_client = None


def _db() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=FIRESTORE_PROJECT_ID)
    return _client


def _ts_to_str(ts) -> str:
    """Convert a Firestore timestamp to dd/mm/YYYY string, or empty string."""
    if ts is None:
        return ""
    try:
        return ts.strftime("%d/%m/%Y")
    except AttributeError:
        return str(ts)


# ─── Familias ────────────────────────────────────────────────────────────────

def get_familia(familia_id: str) -> dict | None:
    doc = _db().collection("familias").document(familia_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def list_familias() -> list[dict]:
    docs = _db().collection("familias").stream()
    result = []
    for doc in docs:
        data = doc.to_dict()
        result.append({
            "id": doc.id,
            "nombre": data.get("nombre", ""),
            "estado": data.get("estado", ""),
            "pack": data.get("pack", ""),
            "integrantes_extra": data.get("integrantes_extra", 0),
            "fecha_compra": _ts_to_str(data.get("fecha_compra")),
            "fecha_entrega": _ts_to_str(data.get("fecha_entrega")),
            "comprador": data.get("comprador", {}),
        })
    return result


def update_familia_estado(familia_id: str, estado: str) -> None:
    _db().collection("familias").document(familia_id).update({"estado": estado})


def save_libro_url(familia_id: str, url: str) -> None:
    _db().collection("familias").document(familia_id).update({"libro_url": url})


# ─── Integrantes ─────────────────────────────────────────────────────────────

def get_integrantes(familia_id: str) -> list[dict]:
    docs = (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .stream()
    )
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(data)
    return result


def get_integrante_by_token(token: str) -> tuple[str, str, dict] | None:
    """
    Search all familias for an integrante whose token_unico matches.
    Returns (familia_id, integrante_id, data) or None if not found.
    """
    familias = _db().collection("familias").stream()
    for familia_doc in familias:
        matches = (
            _db()
            .collection("familias")
            .document(familia_doc.id)
            .collection("integrantes")
            .where("token_unico", "==", token)
            .limit(1)
            .stream()
        )
        for integrante_doc in matches:
            data = integrante_doc.to_dict()
            data["id"] = integrante_doc.id
            return familia_doc.id, integrante_doc.id, data
    return None


def update_integrante_estado(familia_id: str, integrante_id: str, estado: str) -> None:
    (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .update({"estado": estado})
    )


# ─── Respuestas ──────────────────────────────────────────────────────────────

def get_respuestas(familia_id: str, integrante_id: str) -> list[dict]:
    docs = (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .collection("respuestas")
        .order_by("__name__")
        .stream()
    )
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(data)
    return result


def get_transcripciones_integrante(familia_id: str, integrante_id: str) -> list[dict]:
    """Returns [{pregunta: str, transcripcion: str}] — only entries with non-empty transcripcion."""
    respuestas = get_respuestas(familia_id, integrante_id)
    result = []
    for r in respuestas:
        transcripcion = r.get("transcripcion", "").strip()
        if transcripcion:
            result.append({
                "pregunta": r.get("id", ""),
                "transcripcion": transcripcion,
            })
    return result


def save_transcripcion(
    familia_id: str, integrante_id: str, pregunta_id: str, transcripcion: str
) -> None:
    (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .collection("respuestas")
        .document(pregunta_id)
        .update({"transcripcion": transcripcion})
    )


# ─── Perfil de voz y capítulo ────────────────────────────────────────────────

def save_perfil_voz(
    familia_id: str,
    integrante_id: str,
    perfil: dict,
    transcripcion_completa: str,
) -> None:
    (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .update({
            "perfil_voz": perfil,
            "transcripcion_completa": transcripcion_completa,
        })
    )


def save_capitulo(familia_id: str, integrante_id: str, capitulo: str) -> None:
    (
        _db()
        .collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .update({"capitulo": capitulo})
    )


# ─── Para el orchestrator ─────────────────────────────────────────────────────

def get_integrantes_para_pipeline(familia_id: str) -> list[dict]:
    """
    Returns a list of dicts for each integrante with all fields needed by the pipeline:
      nombre, fecha_nac, rol, es_menor, vive, fecha_fallec,
      foto_url, relacion_con_comprador, perfil_voz (dict),
      transcripcion (str), capitulo (str)
    """
    integrantes = get_integrantes(familia_id)
    result = []
    for integrante in integrantes:
        fecha_nac_raw = integrante.get("fecha_nac")
        fecha_nac = _ts_to_str(fecha_nac_raw) if fecha_nac_raw else ""

        fecha_fallec_raw = integrante.get("fecha_fallec")
        fecha_fallec = _ts_to_str(fecha_fallec_raw) if fecha_fallec_raw else ""

        vive = not bool(fecha_fallec)

        perfil_voz = integrante.get("perfil_voz", {})
        if isinstance(perfil_voz, str):
            try:
                perfil_voz = json.loads(perfil_voz)
            except (json.JSONDecodeError, TypeError):
                perfil_voz = {}

        result.append({
            "id": integrante.get("id", ""),
            "nombre": integrante.get("nombre", ""),
            "fecha_nac": fecha_nac,
            "rol": integrante.get("rol", ""),
            "es_menor": integrante.get("es_menor", False),
            "vive": vive,
            "fecha_fallec": fecha_fallec,
            "foto_url": integrante.get("foto_url", ""),
            "relacion_con_comprador": integrante.get("relacion_con_comprador", ""),
            "es_comprador": integrante.get("es_comprador", False),
            "perfil_voz": perfil_voz,
            "transcripcion": integrante.get("transcripcion_completa", ""),
            "capitulo": integrante.get("capitulo", ""),
        })
    return result


def get_relaciones(familia_id: str) -> list[dict]:
    """
    Infer relations from relacion_con_comprador on each integrante.
    The comprador (es_comprador=True) is the reference point.
    Returns [{persona_a, relacion, persona_b}].
    Valid relaciones: padre, madre, cónyuge, esposo, esposa, hijo, hija
    """
    RELACIONES_VALIDAS = {"padre", "madre", "cónyuge", "conyuge", "esposo", "esposa", "hijo", "hija"}

    integrantes = get_integrantes(familia_id)

    comprador_nombre = None
    for integrante in integrantes:
        if integrante.get("es_comprador"):
            comprador_nombre = integrante.get("nombre", "")
            break

    if not comprador_nombre:
        return []

    result = []
    for integrante in integrantes:
        if integrante.get("es_comprador"):
            continue

        relacion = integrante.get("relacion_con_comprador", "").strip().lower()
        if not relacion:
            continue

        nombre_integrante = integrante.get("nombre", "")
        if not nombre_integrante:
            continue

        # Normalize cónyuge variants
        if relacion in ("cónyuge", "conyuge", "esposo", "esposa"):
            relacion_normalizada = relacion
        elif relacion in RELACIONES_VALIDAS:
            relacion_normalizada = relacion
        else:
            continue

        result.append({
            "persona_a": nombre_integrante,
            "relacion": relacion_normalizada,
            "persona_b": comprador_nombre,
        })

    return result


# ─── Onboarding: crear familia e integrantes ─────────────────────────────────

def create_familia(nombre: str, comprador: dict, pack: str, pais: str = "argentina") -> str:
    """Crea un doc de familia y retorna el familia_id (8 chars del UUID)."""
    familia_id = _uuid.uuid4().hex[:8]
    _db().collection("familias").document(familia_id).set({
        "nombre": nombre,
        "comprador": comprador,
        "pack": pack,
        "pais": pais,
        "estado": "onboarding",
        "integrantes_extra": 0,
        "fecha_compra": firestore.SERVER_TIMESTAMP,
        "fecha_entrega": None,
    })
    return familia_id


def add_integrante(
    familia_id: str,
    nombre: str,
    relacion_con_comprador: str,
    es_menor: bool = False,
    fecha_nac: str = "",
    es_comprador: bool = False,
) -> tuple[str, str]:
    """Agrega un integrante a la familia. Retorna (integrante_id, token_unico)."""
    integrante_id = _uuid.uuid4().hex[:8]
    token = str(_uuid.uuid4())
    _db().collection("familias").document(familia_id).collection("integrantes").document(integrante_id).set({
        "nombre": nombre,
        "relacion_con_comprador": relacion_con_comprador,
        "token_unico": token,
        "es_comprador": es_comprador,
        "es_menor": es_menor,
        "fecha_nac": fecha_nac,
        "estado": "pendiente",
        "foto_url": "",
        "porcentaje_avance": 0,
        "ultimo_acceso": None,
    })
    return integrante_id, token


# ─── Jobs (pipeline async) ────────────────────────────────────────────────────

def create_job(job_id: str) -> None:
    from datetime import datetime, timezone
    _db().collection("jobs").document(job_id).set({
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    })


def update_job_status(job_id: str, status: str) -> None:
    _db().collection("jobs").document(job_id).update({"status": status})


def update_job_done(job_id: str, result: dict) -> None:
    _db().collection("jobs").document(job_id).update({
        "status": "done",
        "result": json.dumps(result, ensure_ascii=False),
    })


def update_job_error(job_id: str, error: str) -> None:
    _db().collection("jobs").document(job_id).update({
        "status": "error",
        "error": error,
    })


def get_job(job_id: str) -> dict | None:
    doc = _db().collection("jobs").document(job_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    if data.get("result") and isinstance(data["result"], str):
        try:
            data["result"] = json.loads(data["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return data
