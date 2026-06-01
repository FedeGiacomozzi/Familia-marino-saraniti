"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import base64
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

# ─── Async job store (Firestore) ─────────────────────────────────────────────

def _run_pipeline_job(job_id: str, req_dict: dict) -> None:
    from pipeline.utils import firestore as fs
    fs.update_job_status(job_id, "running")
    try:
        result = orchestrator.run(
            nombres=req_dict["nombres"],
            pais=req_dict["pais"],
            solo_desde=req_dict["solo_desde"],
            familia=req_dict["familia"],
            upload_to_gcs=req_dict["upload_to_gcs"],
            familia_id=req_dict.get("familia_id"),
            from_job_id=req_dict.get("from_job_id"),
        )
        payload = {
            "ok": result.ok,
            "personas": result.personas,
            "transcriber": result.transcriber,
            "voice": {k: v for k, v in result.voice.items()},
            "chapters_generados": list(result.chapters.keys()),
            "chapters": result.chapters,
            "orden": result.editor.orden if result.editor else [],
            "prologo": result.editor.prologo if result.editor else "",
            "epilogo": result.editor.epilogo if result.editor else "",
            "transiciones": result.editor.transiciones if result.editor else {},
            "layout": result.layout,
            "errores": result.errores,
        }
        fs.update_job_done(job_id, payload)
    except Exception as exc:  # noqa: BLE001
        fs.update_job_error(job_id, str(exc))

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
    from_job_id: str | None = None  # reutilizar capítulos de un job anterior


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
    from pipeline.utils import firestore as fs
    job_id = str(uuid.uuid4())
    fs.create_job(job_id)
    t = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, req.model_dump()),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/job/{job_id}")
def get_job_status(job_id: str):
    from pipeline.utils import firestore as fs
    job = fs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    response: dict = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job.get("created_at", ""),
    }
    if job["status"] == "done":
        response["result"] = job.get("result")
    elif job["status"] == "error":
        response["error"] = job.get("error")
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


# ─── Onboarding: Familias ─────────────────────────────────────────────────────

class CompradorInfo(BaseModel):
    email: str
    nombre: str
    es_tambien_retratado: bool = False


class FamiliaRequest(BaseModel):
    nombre: str
    comprador: CompradorInfo
    pack: str = "base"
    pais: str = "argentina"


class IntegranteRequest(BaseModel):
    nombre: str
    relacion_con_comprador: str
    es_menor: bool = False
    fecha_nac: str = ""


@app.post("/familia", status_code=201)
def crear_familia(req: FamiliaRequest):
    from pipeline.utils import firestore as fs

    familia_id = fs.create_familia(
        nombre=req.nombre,
        comprador=req.comprador.model_dump(),
        pack=req.pack,
        pais=req.pais,
    )

    token_comprador = None
    if req.comprador.es_tambien_retratado:
        _, token_comprador = fs.add_integrante(
            familia_id=familia_id,
            nombre=req.comprador.nombre,
            relacion_con_comprador="comprador",
            es_comprador=True,
        )

    return {"familia_id": familia_id, "token_comprador": token_comprador}


@app.post("/familia/{familia_id}/integrantes", status_code=201)
def agregar_integrante(familia_id: str, req: IntegranteRequest):
    from pipeline.utils import firestore as fs

    if not fs.get_familia(familia_id):
        raise HTTPException(status_code=404, detail=f"Familia no encontrada: {familia_id}")

    integrante_id, token = fs.add_integrante(
        familia_id=familia_id,
        nombre=req.nombre,
        relacion_con_comprador=req.relacion_con_comprador,
        es_menor=req.es_menor,
        fecha_nac=req.fecha_nac,
    )
    return {"integrante_id": integrante_id, "token_unico": token}


@app.get("/familia/{familia_id}")
def get_familia_detail(familia_id: str):
    from pipeline.utils import firestore as fs

    familia = fs.get_familia(familia_id)
    if not familia:
        raise HTTPException(status_code=404, detail=f"Familia no encontrada: {familia_id}")

    integrantes = fs.get_integrantes_para_pipeline(familia_id)
    return {
        **familia,
        "integrantes": [
            {
                "id": p["id"],
                "nombre": p["nombre"],
                "relacion_con_comprador": p["relacion_con_comprador"],
                "es_comprador": p["es_comprador"],
                "es_menor": p["es_menor"],
                "estado": "pendiente",
                "porcentaje_avance": 0,
            }
            for p in integrantes
        ],
    }


# ─── Recepción de audio (recording.html / app móvil) ─────────────────────────

class AudioRequest(BaseModel):
    audio_base64: str
    mime_type: str = "audio/webm"


def _check_y_trigger(familia_id: str) -> None:
    """Auto-trigger del pipeline cuando todos los integrantes grabaron."""
    from pipeline.utils import firestore as fs

    integrantes = fs.get_integrantes(familia_id)
    pendientes = [i for i in integrantes if i.get("estado") != "completo"]
    if pendientes:
        return

    familia = fs.get_familia(familia_id) or {}
    nombres = [
        i.get("nombre", "")
        for i in integrantes
        if i.get("nombre") and not i.get("es_menor")
    ]
    job_id = str(uuid.uuid4())
    fs.create_job(job_id)
    t = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, {
            "nombres": nombres,
            "pais": familia.get("pais", "argentina"),
            "solo_desde": None,
            "familia": familia.get("nombre", "Familia Mariño · Saraniti"),
            "upload_to_gcs": True,
            "familia_id": familia_id,
            "from_job_id": None,
        }),
        daemon=True,
    )
    t.start()
    print(f"[auto-trigger] familia {familia_id} completa → job {job_id}")


@app.post("/audio/{token}")
def recibir_audio(token: str, req: AudioRequest):
    """
    Recibe el audio de un integrante (base64), lo sube a GCS,
    marca el integrante como completo y dispara el pipeline si todos grabaron.
    """
    from pipeline.utils import firestore as fs, storage as st

    match = fs.get_integrante_by_token(token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")

    familia_id, integrante_id, _ = match

    audio_bytes = base64.b64decode(req.audio_base64)
    ext = req.mime_type.split("/")[-1].split(";")[0]
    blob_name = (
        f"{familia_id}/{integrante_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.{ext}"
    )

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        gcs_uri = st.upload_to_gcs(tmp_path, st.GCS_BUCKET_AUDIOS, blob_name, req.mime_type)
    finally:
        os.unlink(tmp_path)

    fs.update_integrante_estado(familia_id, integrante_id, "completo")
    _check_y_trigger(familia_id)

    return {"ok": True, "audio_url": gcs_uri}
