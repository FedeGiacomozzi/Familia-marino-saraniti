"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import base64
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

app = FastAPI(title="Familia Libro Pipeline", version="1.0")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Full pipeline (síncrono) ─────────────────────────────────────────────────

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


# ─── Pipeline asíncrono ───────────────────────────────────────────────────────

def _run_pipeline_background(job_id: str, run_kwargs: dict) -> None:
    """BackgroundTask: runs the pipeline and updates the Firestore job."""
    from pipeline.utils import firestore_client

    try:
        result = orchestrator.run(**run_kwargs)
        firestore_client.update_job(job_id, {
            "status": "done",
            "pdf_url": result.layout or "",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        firestore_client.update_job(job_id, {
            "status": "error",
            "error_msg": str(exc),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })


@app.post("/run/pipeline/async")
def run_pipeline_async(req: PipelineRequest, background_tasks: BackgroundTasks):
    from pipeline.utils import firestore_client

    job_id = str(uuid4())
    familia_id = req.familia.replace(" ", "_").lower()
    firestore_client.create_job(job_id, {
        "status": "pending",
        "familia_id": familia_id,
        "nombres": req.nombres,
    })
    background_tasks.add_task(
        _run_pipeline_background,
        job_id,
        {
            "nombres": req.nombres,
            "pais": req.pais,
            "solo_desde": req.solo_desde,
            "familia": req.familia,
            "upload_to_drive": req.upload_to_drive,
        },
    )
    return {"job_id": job_id, "status": "pending"}


@app.get("/job/{job_id}")
def get_job(job_id: str):
    from pipeline.utils import firestore_client
    from pipeline.utils import gcs_client

    job = firestore_client.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    # Enrich pdf_url with a fresh 7-day signed URL when job is done
    if job.get("status") == "done" and job.get("pdf_url", "").startswith("gs://"):
        try:
            job["pdf_url"] = gcs_client.signed_url_from_gs_uri(job["pdf_url"])
        except Exception:
            pass  # leave the gs:// URI as-is if signing fails

    return job


# ─── Recepción de audio (recording.html) ─────────────────────────────────────

class AudioRequest(BaseModel):
    familia_id: str
    audio_base64: str
    mime_type: str = "audio/webm"


def _check_y_trigger(familia_id: str, background_tasks: BackgroundTasks) -> None:
    """Auto-trigger the pipeline when every token for a family is completed."""
    from pipeline.utils import firestore_client

    tokens = firestore_client.get_tokens_familia(familia_id)
    pendientes = [t for t in tokens if not t.get("completado")]
    if len(pendientes) > 0:
        return

    familia = firestore_client.get_familia(familia_id)
    nombres = [t["nombre"] for t in tokens if t.get("nombre")]
    job_id = str(uuid4())
    firestore_client.create_job(job_id, {
        "familia_id": familia_id,
        "status": "pending",
        "nombres": nombres,
    })
    background_tasks.add_task(
        _run_pipeline_background,
        job_id,
        {
            "nombres": nombres,
            "pais": familia.get("pais", "argentina") if familia else "argentina",
            "solo_desde": None,
            "familia": familia.get("nombre", "Familia Mariño · Saraniti") if familia else "Familia Mariño · Saraniti",
            "upload_to_drive": True,
        },
    )
    print(f"[auto-trigger] Todos grabaron en familia {familia_id} → job {job_id}")


@app.post("/audio/{token}")
def recibir_audio(token: str, req: AudioRequest, background_tasks: BackgroundTasks):
    from pipeline.utils import firestore_client, gcs_client

    audio_bytes = base64.b64decode(req.audio_base64)
    ext = req.mime_type.split("/")[-1].split(";")[0]  # e.g. "webm"
    blob_name = f"{req.familia_id}/{token}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.{ext}"

    gcs_uri = gcs_client.upload_audio(audio_bytes, blob_name, req.mime_type)
    firestore_client.mark_token_completado(req.familia_id, token, gcs_uri)
    _check_y_trigger(req.familia_id, background_tasks)

    return {"ok": True, "audio_url": gcs_uri}


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
