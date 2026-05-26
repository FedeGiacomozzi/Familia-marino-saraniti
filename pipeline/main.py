"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import io
import json
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

app = FastAPI(title="Familia Libro Pipeline", version="1.0")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Ingest helpers ───────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_gcp_creds(scopes: list[str]):
    from google.oauth2 import service_account
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    import google.auth
    creds, _ = google.auth.default(scopes=scopes)
    return creds


def _drive_service():
    from googleapiclient.discovery import build
    creds = _get_gcp_creds([
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/cloud-platform",
    ])
    return build("drive", "v3", credentials=creds)


def _firestore_client():
    from google.cloud import firestore
    creds = _get_gcp_creds(["https://www.googleapis.com/auth/cloud-platform"])
    return firestore.Client(project="familia-marino", credentials=creds)


def _gcs_client():
    from google.cloud import storage
    creds = _get_gcp_creds(["https://www.googleapis.com/auth/cloud-platform"])
    return storage.Client(project="familia-marino", credentials=creds)


def _extract_file_id(url: str) -> str | None:
    for pattern in [r"/d/([a-zA-Z0-9_-]+)", r"id=([a-zA-Z0-9_-]+)"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _download_drive_bytes(drive, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


# ─── Ingest audio (llamado desde Apps Script) ─────────────────────────────────

class IngestAudioRequest(BaseModel):
    familia_id: str = "marino-saraniti"
    nombre: str
    fecha_nac: str = ""
    pregunta_id: str
    drive_url: str
    mime_type: str = "audio/webm"


@app.post("/ingest-audio")
def ingest_audio(req: IngestAudioRequest):
    integrante_id = _slug(req.nombre)

    # 1. Descargar de Drive
    drive = _drive_service()
    file_id = _extract_file_id(req.drive_url)
    if not file_id:
        raise HTTPException(400, f"No se pudo extraer el file ID de: {req.drive_url}")
    try:
        audio_bytes = _download_drive_bytes(drive, file_id)
    except Exception as e:
        raise HTTPException(500, f"Error descargando de Drive: {e}")

    # 2. Subir a GCS
    try:
        gcs = _gcs_client()
        blob_name = f"{req.familia_id}/{integrante_id}/{req.pregunta_id}"
        gcs.bucket("libro-familiar-audios").blob(blob_name).upload_from_string(
            audio_bytes, content_type=req.mime_type
        )
        gcs_path = f"gs://libro-familiar-audios/{blob_name}"
    except Exception as e:
        raise HTTPException(500, f"Error subiendo a GCS: {e}")

    # 3. Escribir en Firestore (merge=True en todos los niveles → idempotente)
    try:
        db = _firestore_client()
        familia_ref = db.collection("familias").document(req.familia_id)
        familia_ref.set({"estado": "activo"}, merge=True)

        integrante_ref = familia_ref.collection("integrantes").document(integrante_id)
        integrante_ref.set(
            {
                "nombre": req.nombre,
                "fecha_nac": req.fecha_nac,
                "token_unico": str(uuid.uuid5(uuid.NAMESPACE_DNS, integrante_id)),
                "estado": "en_proceso",
                "es_comprador": False,
            },
            merge=True,
        )

        integrante_ref.collection("respuestas").document(req.pregunta_id).set(
            {
                "audio_url": gcs_path,
                "transcripcion": "",
                "timestamp": _now_iso(),
                "drive_url_origen": req.drive_url,
            },
            merge=True,
        )
    except Exception as e:
        raise HTTPException(500, f"Error escribiendo en Firestore: {e}")

    return {"ok": True, "gcs_path": gcs_path, "integrante_id": integrante_id}


# ─── Ingest foto (llamado desde Apps Script) ──────────────────────────────────

class IngestFotoRequest(BaseModel):
    familia_id: str = "marino-saraniti"
    nombre: str
    drive_url: str
    mime_type: str = "image/jpeg"


@app.post("/ingest-foto")
def ingest_foto(req: IngestFotoRequest):
    integrante_id = _slug(req.nombre)

    drive = _drive_service()
    file_id = _extract_file_id(req.drive_url)
    if not file_id:
        raise HTTPException(400, f"No se pudo extraer el file ID de: {req.drive_url}")
    try:
        foto_bytes = _download_drive_bytes(drive, file_id)
    except Exception as e:
        raise HTTPException(500, f"Error descargando foto de Drive: {e}")

    try:
        gcs = _gcs_client()
        ext = req.mime_type.split("/")[-1] if "/" in req.mime_type else "jpg"
        blob_name = f"{req.familia_id}/{integrante_id}/foto.{ext}"
        gcs.bucket("libro-familiar-fotos").blob(blob_name).upload_from_string(
            foto_bytes, content_type=req.mime_type
        )
        gcs_path = f"gs://libro-familiar-fotos/{blob_name}"
    except Exception as e:
        raise HTTPException(500, f"Error subiendo foto a GCS: {e}")

    try:
        db = _firestore_client()
        (
            db.collection("familias")
            .document(req.familia_id)
            .collection("integrantes")
            .document(integrante_id)
            .set({"foto_url": gcs_path}, merge=True)
        )
    except Exception as e:
        raise HTTPException(500, f"Error escribiendo foto en Firestore: {e}")

    return {"ok": True, "gcs_path": gcs_path, "integrante_id": integrante_id}


# ─── Full pipeline ────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nombres: list[str]
    pais: str = "argentina"
    solo_desde: str | None = None
    familia: str = "Familia Mariño · Saraniti"
    upload_to_drive: bool = False


@app.post("/run/pipeline")
def run_pipeline(req: PipelineRequest):
    result = orchestrator.run(
        nombres=req.nombres,
        pais=req.pais,
        solo_desde=req.solo_desde,
        familia=req.familia,
        upload_to_drive=req.upload_to_drive,
    )
    return {
        "ok": result.ok,
        "personas": result.personas,
        "transcriber": result.transcriber,
        "voice": {k: v for k, v in result.voice.items()},
        "chapters_generados": list(result.chapters.keys()),
        "orden": result.editor.orden if result.editor else [],
        "layout": result.layout,
        "errores": result.errores,
    }


# ─── Paso 1: Transcriber ──────────────────────────────────────────────────────

class TranscriberRequest(BaseModel):
    row_indices: list[int]
    pais: str = "argentina"


@app.post("/run/transcriber")
def run_transcriber(req: TranscriberRequest):
    result = transcriber.run(req.row_indices, req.pais)
    return result


# ─── Paso 2: Voice agent ──────────────────────────────────────────────────────

class NombresRequest(BaseModel):
    nombres: list[str]


@app.post("/run/voice")
def run_voice(req: NombresRequest):
    result = voice_agent.run(req.nombres)
    return result


# ─── Paso 3: Chapters ─────────────────────────────────────────────────────────

@app.post("/run/chapters")
def run_chapters(req: NombresRequest):
    result = chapter_agent.run(req.nombres)
    return {"chapters": {k: len(v) for k, v in result.items()}}


# ─── Paso 4: Editor ───────────────────────────────────────────────────────────

class EditorRequest(BaseModel):
    nombres: list[str]


@app.post("/run/editor")
def run_editor(req: EditorRequest):
    from pipeline.agents import editor_agent

    personas_meta = []
    capitulos = {}
    for nombre in req.nombres:
        p = sheets.get_profile(nombre)
        if not p:
            raise HTTPException(status_code=404, detail=f"Perfil no encontrado: {nombre}")
        personas_meta.append(
            {
                "nombre": nombre,
                "fecha_nac": sheets.get_fecha_nac(nombre),
                "perfil_voz": p.get("perfil_voz", {}),
            }
        )
        capitulos[nombre] = p.get("capitulo", "")

    manuscript = editor_agent.run(personas_meta, capitulos)
    return {
        "orden": manuscript.orden,
        "prologo_chars": len(manuscript.prologo),
        "epilogo_chars": len(manuscript.epilogo),
        "transiciones": list(manuscript.transiciones.keys()),
    }


# ─── Paso 5: Layout ───────────────────────────────────────────────────────────

class LayoutRequest(BaseModel):
    nombres: list[str]
    familia: str = "Familia Mariño · Saraniti"
    upload_to_drive: bool = False


@app.post("/run/layout")
def run_layout(req: LayoutRequest):
    from pipeline.agents import editor_agent

    personas_meta = []
    capitulos = {}
    for nombre in req.nombres:
        p = sheets.get_profile(nombre)
        if not p:
            raise HTTPException(status_code=404, detail=f"Perfil no encontrado: {nombre}")
        personas_meta.append(
            {
                "nombre": nombre,
                "fecha_nac": sheets.get_fecha_nac(nombre),
                "perfil_voz": p.get("perfil_voz", {}),
            }
        )
        capitulos[nombre] = p.get("capitulo_revisado") or p.get("capitulo", "")

    manuscript = editor_agent.run(personas_meta, capitulos)
    pdf_path = layout_agent.run(
        manuscript=manuscript,
        personas_meta=personas_meta,
        nombre_familia=req.familia,
    )

    if req.upload_to_drive:
        drive_url = sheets.upload_to_drive(pdf_path, os.path.basename(pdf_path), "application/pdf")
        return {"pdf": drive_url, "uploaded": True}

    return {"pdf": pdf_path, "uploaded": False}
