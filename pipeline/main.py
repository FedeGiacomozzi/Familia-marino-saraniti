"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import subprocess

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets
from pipeline.utils import firestore as fstore
from pipeline.utils import storage

app = FastAPI(title="Familia Libro Pipeline", version="1.0")


# ─── Health + version ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        commit = "unknown"
    return {"status": "ok", "commit": commit}


@app.get("/debug/integrantes")
def debug_integrantes():
    """Lista los nombres exactos de integrantes en Firestore (para diagnóstico)."""
    integrantes = fstore.get_familia_integrantes()
    return {"count": len(integrantes), "integrantes": integrantes}


class IntegranteUpdate(BaseModel):
    nombre: str
    fecha_nac: str | None = None
    fecha_fallec: str | None = None
    rol: str | None = None
    es_menor: bool | None = None


@app.post("/update/integrante")
def update_integrante(req: IntegranteUpdate):
    """Actualiza campos de un integrante en Firestore (fecha_nac, rol, etc.)."""
    fields = {k: v for k, v in req.model_dump().items() if k != "nombre" and v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No hay campos para actualizar")
    fstore.update_integrante_fields(req.nombre, fields)
    return {"ok": True, "nombre": req.nombre, "fields": fields}


@app.get("/libro/{familia_id}")
def get_libro(familia_id: str):
    """Genera una URL firmada de 7 días para descargar el libro de la familia."""
    gcs_path = fstore.get_libro_gcs_path(familia_id)
    if not gcs_path:
        raise HTTPException(status_code=404, detail=f"No hay libro generado para {familia_id}")
    signed_url = storage.get_signed_url(gcs_path, expiration_days=7)
    return RedirectResponse(url=signed_url)


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
