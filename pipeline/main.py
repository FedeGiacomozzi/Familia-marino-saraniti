"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import json
import queue
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

app = FastAPI(title="Familia Libro Pipeline", version="1.0")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/familia")
def debug_familia():
    return {
        "integrantes": sheets.get_familia_integrantes(),
        "relaciones": sheets.get_familia_relaciones(),
    }


# ─── Full pipeline ────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nombres: list[str]
    pais: str = "argentina"
    solo_desde: str | None = None
    familia: str = "Familia Mariño · Saraniti"
    upload_to_drive: bool = False
    solo_nuevas: bool = False


@app.post("/run/pipeline")
def run_pipeline(req: PipelineRequest):
    result = orchestrator.run(
        nombres=req.nombres,
        pais=req.pais,
        solo_desde=req.solo_desde,
        familia=req.familia,
        upload_to_drive=req.upload_to_drive,
        solo_nuevas=req.solo_nuevas,
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


@app.post("/run/pipeline/stream")
def run_pipeline_stream(req: PipelineRequest):
    """Same as /run/pipeline but streams progress lines as they happen."""
    progress_queue: queue.Queue = queue.Queue()

    def on_progress(msg: str):
        progress_queue.put({"type": "progress", "msg": msg})

    def run_in_thread():
        try:
            result = orchestrator.run(
                nombres=req.nombres,
                pais=req.pais,
                solo_desde=req.solo_desde,
                familia=req.familia,
                upload_to_drive=req.upload_to_drive,
                solo_nuevas=req.solo_nuevas,
                on_progress=on_progress,
            )
            progress_queue.put({"type": "result", "data": {
                "ok": result.ok,
                "personas": result.personas,
                "transcriber": result.transcriber,
                "chapters_generados": list(result.chapters.keys()),
                "orden": result.editor.orden if result.editor else [],
                "layout": result.layout,
                "errores": result.errores,
            }})
        except Exception as e:
            progress_queue.put({"type": "error", "msg": str(e)})
        finally:
            progress_queue.put(None)

    threading.Thread(target=run_in_thread, daemon=True).start()

    def generate():
        while True:
            item = progress_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ─── Paso 1: Transcriber ──────────────────────────────────────────────────────

class TranscriberRequest(BaseModel):
    row_indices: list[int]
    pais: str = "argentina"
    solo_nuevas: bool = False


@app.post("/run/transcriber")
def run_transcriber(req: TranscriberRequest):
    result = transcriber.run(req.row_indices, req.pais, solo_nuevas=req.solo_nuevas)
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
