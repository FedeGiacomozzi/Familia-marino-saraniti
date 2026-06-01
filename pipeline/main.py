"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

logger = logging.getLogger(__name__)

app = FastAPI(title="Familia Libro Pipeline", version="1.0")

RECORDING_BASE_URL = "https://fedegiacomozzi.github.io/familia-marino/recording.html"


def _get_firestore():
    import firebase_admin
    from firebase_admin import credentials, firestore
    from pipeline.utils.secrets import get_secret

    if not firebase_admin._apps:
        cred_raw = get_secret("GOOGLE_CREDENTIALS_JSON")
        try:
            info = json.loads(cred_raw)
        except (json.JSONDecodeError, ValueError):
            with open(cred_raw) as f:
                info = json.load(f)
        cred = credentials.Certificate(info)
        firebase_admin.initialize_app(cred)

    return firestore.client()


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


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
        import os
        drive_url = sheets.upload_to_drive(pdf_path, os.path.basename(pdf_path), "application/pdf")
        return {"pdf": drive_url, "uploaded": True}

    return {"pdf": pdf_path, "uploaded": False}


# ─── Onboarding: crear familia y tokens ──────────────────────────────────────

class IntegranteIn(BaseModel):
    nombre: str
    relacion: str


class FamiliaRequest(BaseModel):
    nombre_familia: str
    email_comprador: str
    pais: str = "argentina"
    integrantes: list[IntegranteIn]


@app.post("/familia")
def crear_familia(req: FamiliaRequest):
    if not req.integrantes:
        raise HTTPException(status_code=400, detail="Debe haber al menos un integrante.")

    db = _get_firestore()

    familia_id = str(uuid4())
    db.collection("familias").document(familia_id).set({
        "nombre_familia":  req.nombre_familia,
        "email_comprador": req.email_comprador,
        "pais":            req.pais,
        "created_at":      datetime.now(timezone.utc).isoformat(),
    })

    tokens_result = []
    for integrante in req.integrantes:
        token = str(uuid4())[:8]
        link  = f"{RECORDING_BASE_URL}?token={token}"
        db.collection("tokens").document(token).set({
            "nombre":     integrante.nombre,
            "relacion":   integrante.relacion,
            "familia_id": familia_id,
            "completado": False,
        })
        tokens_result.append({
            "nombre":   integrante.nombre,
            "relacion": integrante.relacion,
            "token":    token,
            "link":     link,
        })

    return {"familia_id": familia_id, "tokens": tokens_result}
