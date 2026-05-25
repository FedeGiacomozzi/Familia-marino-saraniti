import json
import os
import uuid

from google.cloud import firestore
from google.oauth2 import service_account

PROJECT_ID = "familia-marino"

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _get_creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
    else:
        path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "/secrets/credentials.json")
        with open(path) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)


def _db() -> firestore.Client:
    return firestore.Client(project=PROJECT_ID, credentials=_get_creds())


def crear_familia(familia_data: dict) -> str:
    db = _db()
    familia_id = familia_data.get("familia_id") or str(uuid.uuid4())
    db.collection("familias").document(familia_id).set(familia_data)
    return familia_id


def agregar_integrante(familia_id: str, integrante_data: dict) -> str:
    db = _db()
    integrante_id = integrante_data.get("integrante_id") or str(uuid.uuid4())
    (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .set(integrante_data)
    )
    return integrante_id


def guardar_respuesta(
    familia_id: str,
    integrante_id: str,
    pregunta_id: str,
    respuesta_data: dict,
):
    db = _db()
    (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .collection("respuestas")
        .document(pregunta_id)
        .set(respuesta_data)
    )


def get_familia(familia_id: str) -> dict:
    db = _db()
    doc = db.collection("familias").document(familia_id).get()
    if not doc.exists:
        return {}
    return {"familia_id": doc.id, **doc.to_dict()}


def get_integrantes(familia_id: str) -> list:
    db = _db()
    docs = (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .stream()
    )
    return [{"integrante_id": d.id, **d.to_dict()} for d in docs]


def get_respuestas(familia_id: str, integrante_id: str) -> list:
    db = _db()
    docs = (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .collection("respuestas")
        .stream()
    )
    return [{"pregunta_id": d.id, **d.to_dict()} for d in docs]


def actualizar_estado_familia(familia_id: str, estado: str):
    db = _db()
    db.collection("familias").document(familia_id).update({"estado": estado})


def actualizar_progreso_integrante(familia_id: str, integrante_id: str, porcentaje: int):
    db = _db()
    (
        db.collection("familias")
        .document(familia_id)
        .collection("integrantes")
        .document(integrante_id)
        .update({"porcentaje_avance": porcentaje})
    )


def get_familias_activas() -> list:
    db = _db()
    docs = (
        db.collection("familias")
        .where(filter=firestore.FieldFilter("estado", "!=", "entregado"))
        .stream()
    )
    return [{"familia_id": d.id, **d.to_dict()} for d in docs]
