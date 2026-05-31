"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

# ─── Async job store ──────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _cleanup_old_jobs() -> None:
    """Remove jobs older than 2 hours."""
    cutoff = time.time() - 7200
    with _jobs_lock:
        to_delete = [
            jid for jid, job in _jobs.items()
            if datetime.fromisoformat(job["created_at"]).timestamp() < cutoff
        ]
        for jid in to_delete:
            del _jobs[jid]


def _run_pipeline_job(job_id: str, req_dict: dict) -> None:
    _cleanup_old_jobs()
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
    try:
        result = orchestrator.run(
            nombres=req_dict["nombres"],
            pais=req_dict["pais"],
            solo_desde=req_dict["solo_desde"],
            familia=req_dict["familia"],
            upload_to_gcs=req_dict["upload_to_gcs"],
            familia_id=req_dict.get("familia_id"),
        )
        payload = {
            "ok": result.ok,
            "personas": result.personas,
            "transcriber": result.transcriber,
            "voice": {k: v for k, v in result.voice.items()},
            "chapters_generados": list(result.chapters.keys()),
            "orden": result.editor.orden if result.editor else [],
            "layout": result.layout,
            "errores": result.errores,
        }
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = payload
    except Exception as exc:  # noqa: BLE001
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)

app = FastAPI(title="Familia Libro Pipeline", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fedegiacomozzi.github.io"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/deep")
def health_deep():
    import os
    import time as _time

    checks: dict[str, dict] = {}

    # 1. Sheets (gspread): read first row
    t0 = _time.monotonic()
    try:
        sheets.get_all_nombres()  # lightweight read; raises on auth/network errors
        checks["sheets"] = {"ok": True, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None}
    except Exception as exc:  # noqa: BLE001
        checks["sheets"] = {"ok": False, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": str(exc)}

    # 2. Anthropic: minimal message
    t0 = _time.monotonic()
    try:
        import anthropic as _anthropic
        _client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        checks["anthropic"] = {"ok": True, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None}
    except Exception as exc:  # noqa: BLE001
        checks["anthropic"] = {"ok": False, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": str(exc)}

    # 3. GCS: list bucket familia-marino-pdfs
    t0 = _time.monotonic()
    try:
        from google.cloud import storage as _gcs
        _gcs_client = _gcs.Client()
        _bucket = _gcs_client.get_bucket("familia-marino-pdfs")
        _ = _bucket.name  # forces the API call
        checks["gcs"] = {"ok": True, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None}
    except Exception as exc:  # noqa: BLE001
        checks["gcs"] = {"ok": False, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": str(exc)}

    # 4. OpenAI: list models
    t0 = _time.monotonic()
    try:
        import openai as _openai
        _openai_client = _openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        _openai_client.models.list()
        checks["openai"] = {"ok": True, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None}
    except Exception as exc:  # noqa: BLE001
        checks["openai"] = {"ok": False, "latency_ms": int((_time.monotonic() - t0) * 1000), "error": str(exc)}

    overall_ok = all(v["ok"] for v in checks.values())
    return {"ok": overall_ok, "checks": checks}


# ─── Full pipeline ────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nombres: list[str]
    pais: str = "argentina"
    solo_desde: str | None = None
    familia: str = "Familia Mariño · Saraniti"
    upload_to_gcs: bool = False
    familia_id: str | None = None


@app.post("/run/pipeline")
def run_pipeline(req: PipelineRequest):
    result = orchestrator.run(
        nombres=req.nombres,
        pais=req.pais,
        solo_desde=req.solo_desde,
        familia=req.familia,
        upload_to_gcs=req.upload_to_gcs,
        familia_id=req.familia_id,
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


@app.post("/run/pipeline/async")
def run_pipeline_async(req: PipelineRequest):
    job_id = str(uuid.uuid4())
    job: dict = {
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job

    t = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, req.model_dump()),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/job/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    response: dict = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
    }
    if job["status"] == "done":
        response["result"] = job["result"]
    elif job["status"] == "error":
        response["error"] = job["error"]
    return response


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
    upload_to_gcs: bool = False


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

    if req.upload_to_gcs:
        import os
        gcs_url = sheets.upload_to_gcs(pdf_path, os.path.basename(pdf_path), "application/pdf")
        return {"pdf": gcs_url, "uploaded": True}

    return {"pdf": pdf_path, "uploaded": False}
