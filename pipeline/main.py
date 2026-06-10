"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger(__name__)

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
        familia_id_job = req_dict.get("familia_id")
        if familia_id_job and result.ok:
            layout_url = result.layout or ""
            if layout_url.startswith("gs://"):
                fs.save_libro_url(familia_id_job, layout_url)
            fs.update_familia_estado(familia_id_job, "entregado")
    except Exception as exc:  # noqa: BLE001
        fs.update_job_error(job_id, str(exc))

def _admin_auth(x_admin_key: str = Header(...)) -> None:
    pwd = os.environ.get("ADMIN_PASSWORD", "")
    if not pwd or x_admin_key != pwd:
        raise HTTPException(status_code=401, detail="No autorizado")


# ─── Session helpers (itsdangerous cookie) ───────────────────────────────────

_SESSION_COOKIE = "session"
_SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days


def _session_serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET no configurado")
    return URLSafeTimedSerializer(secret, salt="session")


def _sign_session(familia_id: str) -> str:
    return _session_serializer().dumps({"familia_id": familia_id})


def _verify_session(cookie_value: str) -> str | None:
    """Returns familia_id if cookie is valid and not expired, None otherwise."""
    try:
        data = _session_serializer().loads(cookie_value, max_age=_SESSION_MAX_AGE)
        return data.get("familia_id")
    except (BadSignature, Exception):
        return None


# ─── Stripe webhook signature verification ───────────────────────────────────

def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    timestamp: int | None = None
    v1_sigs: list[str] = []
    for part in sig_header.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip() == "t":
            try:
                timestamp = int(v.strip())
            except ValueError:
                pass
        elif k.strip() == "v1":
            v1_sigs.append(v.strip())

    if timestamp is None or not v1_sigs:
        return False

    if abs(_time.time() - timestamp) > 300:  # 5-minute tolerance
        return False

    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in v1_sigs)


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Familia Libro Pipeline", version="1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://fedegiacomozzi.github.io",
        "https://ethosbios.com",
        "https://www.ethosbios.com",
        "https://ethosbios.vercel.app",
    ],
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

    # 3. GCS: verificar bucket de libros
    t0 = _time.monotonic()
    try:
        from google.cloud import storage as _gcs
        from pipeline.utils.storage import GCS_BUCKET_LIBROS
        _gcs_client = _gcs.Client()
        _bucket = _gcs_client.get_bucket(GCS_BUCKET_LIBROS)
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


# ─── Redirect de token de grabación ──────────────────────────────────────────

@app.get("/r/{token}")
def redirect_token(token: str):
    from pipeline.utils import firestore as fs
    if fs.get_integrante_by_token(token) is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")
    return RedirectResponse(url=f"/recording?token={token}")


@app.get("/recording")
def serve_recording():
    return FileResponse(_STATIC_DIR / "recording.html")


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
    from pipeline.utils.tasks import enqueue_pipeline
    job_id = str(uuid.uuid4())
    fs.create_job(job_id, familia_id=req.familia_id)
    task_name = enqueue_pipeline(job_id, req.model_dump())
    return {"job_id": job_id, "status": "pending", "task_name": task_name}


class WorkerRequest(PipelineRequest):
    job_id: str


@app.post("/run/pipeline/worker")
def run_pipeline_worker(
    req: WorkerRequest,
    x_cloudtasks_queuename: str | None = Header(default=None),
):
    expected = os.environ.get("CLOUD_TASKS_QUEUE", "pipeline-jobs")
    if x_cloudtasks_queuename != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    _run_pipeline_job(req.job_id, req.model_dump())
    return {"ok": True, "job_id": req.job_id}


@app.get("/job/{job_id}")
def get_job_status(job_id: str):
    from pipeline.utils import firestore as fs, storage as st
    job = fs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    response: dict = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job.get("created_at", ""),
        "familia_id": job.get("familia_id"),
    }
    if job["status"] == "done":
        result = job.get("result") or {}
        gs_url = result.get("layout", "")
        pdf_url = None
        if gs_url and gs_url.startswith("gs://"):
            try:
                pdf_url = st.get_signed_url(gs_url, expiration_hours=168)  # 7 días
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo generar signed URL para %s: %s", gs_url, exc)
                pdf_url = gs_url
        response["pdf_url"] = pdf_url
        response["result"] = result
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


# ─── Onboarding unificado (usado por onboarding.html) ────────────────────────

class OnboardingIntegranteRequest(BaseModel):
    nombre: str
    email: str = ""
    rol: str = ""
    fecha_nac: str = ""
    es_menor: bool = False
    pais: str = "argentina"


class OnboardingRequest(BaseModel):
    nombre_familia: str
    email_comprador: str
    integrantes: list[OnboardingIntegranteRequest]
    relaciones: list = []


def _recording_base() -> str:
    return os.environ.get(
        "BASE_URL",
        os.environ.get("CLOUD_RUN_URL", "https://familia-pipeline-776445604502.southamerica-east1.run.app"),
    )


@app.post("/onboarding", status_code=201)
@limiter.limit("5/hour")
async def onboarding(request: Request, req: OnboardingRequest):
    """
    Crea la familia e integrantes en Firestore y devuelve los tokens de grabación.
    familia_id siempre generado en servidor. Tokens generados en servidor vía add_integrante().
    Idempotente por nombre de integrante. Rate-limited: 5 req/IP/hora.
    """
    from google.cloud import firestore as _firestore
    from pipeline.utils import firestore as fs

    familia_id = uuid.uuid4().hex[:8]
    db = fs._db()

    # Upsert familia
    db.collection("familias").document(familia_id).set(
        {
            "nombre": req.nombre_familia,
            "comprador": {
                "email": req.email_comprador,
                "nombre": "",
                "es_tambien_retratado": False,
            },
            "estado": "onboarding",
            "pack": "base",
            "pais": req.integrantes[0].pais if req.integrantes else "argentina",
            "integrantes_extra": max(0, len(req.integrantes) - 4),
            "fecha_compra": _firestore.SERVER_TIMESTAMP,
            "fecha_entrega": None,
        },
        merge=True,
    )

    # Índice de integrantes existentes por nombre para idempotencia
    existentes = {
        i.get("nombre", "").lower(): i
        for i in fs.get_integrantes(familia_id)
    }

    base = _recording_base()
    tokens = []

    for ing in req.integrantes:
        existing = existentes.get(ing.nombre.lower())
        if existing:
            token = existing.get("token_unico", "")
        else:
            integrante_id, token = fs.add_integrante(
                familia_id=familia_id,
                nombre=ing.nombre,
                relacion_con_comprador=ing.rol,
                es_menor=ing.es_menor,
                fecha_nac=ing.fecha_nac,
            )
            # Campos extra que add_integrante no acepta
            db.collection("familias").document(familia_id) \
              .collection("integrantes").document(integrante_id) \
              .update({"email": ing.email, "pais": ing.pais})

        tokens.append({
            "nombre": ing.nombre,
            "link": f"{base}/r/{token}",
            "token": token,
        })

    return {"familia_id": familia_id, "tokens": tokens}


@app.post("/familia/{familia_id}/foto-portada")
async def foto_portada(familia_id: str, file: UploadFile = File(...)):
    """Sube la foto de portada del libro a GCS y guarda la URL en Firestore."""
    from pipeline.utils import firestore as fs, storage as st

    if not fs.get_familia(familia_id):
        raise HTTPException(status_code=404, detail=f"Familia no encontrada: {familia_id}")

    filename = file.filename or "portada.jpg"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    blob_name = f"{familia_id}/portada.{ext}"

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        gs_url = st.upload_to_gcs(tmp_path, st.GCS_BUCKET_FOTOS, blob_name, file.content_type or "image/jpeg")
    finally:
        os.unlink(tmp_path)

    fs._db().collection("familias").document(familia_id).update({"foto_portada_url": gs_url})
    return {"ok": True, "foto_portada_url": gs_url}


@app.get("/familia/{familia_id}/tokens-estado")
def tokens_estado(familia_id: str):
    """Devuelve estado actual de cada token para el dashboard de onboarding.html."""
    from pipeline.utils import firestore as fs

    if not fs.get_familia(familia_id):
        raise HTTPException(status_code=404, detail=f"Familia no encontrada: {familia_id}")

    integrantes = fs.get_integrantes(familia_id)
    base = _recording_base()

    tokens = []
    for i in integrantes:
        token = i.get("token_unico", "")
        tokens.append({
            "nombre": i.get("nombre", ""),
            "link": f"{base}/r/{token}" if token else "",
            "estado": i.get("estado", "pendiente"),
            "usado": i.get("ultimo_acceso") is not None,
            "email": i.get("email", ""),
            "token": token,
        })

    return {"tokens": tokens}


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


def _enviar_email_generando(familia_id: str, job_id: str) -> None:
    """Stub: notifica al comprador que el libro está siendo generado."""
    from pipeline.utils import firestore as fs
    familia = fs.get_familia(familia_id) or {}
    comprador = familia.get("comprador", {})
    comprador_email = comprador.get("email", "")
    comprador_nombre = comprador.get("nombre", "")
    logger.info(
        "[email-generando] familia_id=%s job_id=%s email=%s nombre=%s",
        familia_id, job_id, comprador_email, comprador_nombre,
    )
    # TODO: integrar proveedor de email (SendGrid / Resend)
    # Asunto: "¡Todos grabaron! Tu libro está siendo creado."
    # Cuerpo:
    #   - ¡Todos los integrantes grabaron! Tu libro familiar está siendo creado.
    #   - Seguí el progreso en: /mi-familia?id={familia_id}
    #   - Tiempo estimado: ~40 minutos


def _check_y_trigger(familia_id: str) -> None:
    """Auto-trigger del pipeline cuando todos los integrantes grabaron."""
    from pipeline.utils import firestore as fs
    from pipeline.utils.tasks import enqueue_pipeline

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
    fs.create_job(job_id, familia_id=familia_id)
    enqueue_pipeline(
        job_id,
        {
            "nombres": nombres,
            "pais": familia.get("pais", "argentina"),
            "solo_desde": None,
            "familia": familia.get("nombre", "Familia Mariño · Saraniti"),
            "upload_to_gcs": True,
            "familia_id": familia_id,
            "from_job_id": None,
        },
    )
    fs.update_familia_estado(familia_id, "generando")
    _enviar_email_generando(familia_id, job_id)
    logger.info("[auto-trigger] familia %s completa → job %s", familia_id, job_id)


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


# ─── Endpoints de grabación (token-based, usados por recording.html) ─────────

@app.get("/token/{token}/info")
def token_info(token: str):
    from pipeline.utils import firestore as fs
    match = fs.get_integrante_by_token(token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")
    familia_id, _, data = match
    familia = fs.get_familia(familia_id)
    return {
        "nombre": data.get("nombre", ""),
        "nombre_familia": familia.get("nombre", "") if familia else "",
    }


@app.post("/token/{token}/foto")
async def token_foto(token: str, foto: UploadFile = File(...)):
    from pipeline.utils import firestore as fs, storage as st
    match = fs.get_integrante_by_token(token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")
    familia_id, integrante_id, _ = match

    filename = foto.filename or "foto.jpg"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    blob_name = f"{familia_id}/{integrante_id}/foto.{ext}"

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(await foto.read())
        tmp_path = tmp.name

    try:
        gs_url = st.upload_to_gcs(tmp_path, st.GCS_BUCKET_FOTOS, blob_name, foto.content_type or "image/jpeg")
    finally:
        os.unlink(tmp_path)

    fs.update_integrante_foto(familia_id, integrante_id, gs_url)
    return {"ok": True}


_ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mpeg", "audio/mp4",
    "audio/ogg", "audio/wav", "audio/x-m4a",
}
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB


@app.post("/token/{token}/respuesta")
async def token_respuesta(
    token: str,
    pregunta: str = Form(...),
    audio: UploadFile = File(...),
):
    from pipeline.utils import firestore as fs, storage as st
    match = fs.get_integrante_by_token(token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")
    familia_id, integrante_id, _ = match

    # Fix 4a: validar MIME type
    content_type = (audio.content_type or "").split(";")[0].strip().lower()
    if content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Tipo de archivo no permitido: {content_type!r}. Se aceptan: {', '.join(sorted(_ALLOWED_AUDIO_TYPES))}",
        )

    # Fix 4b: leer y validar tamaño
    audio_bytes = await audio.read()
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Archivo demasiado grande ({len(audio_bytes) / 1024 / 1024:.1f} MB). Máximo permitido: 25 MB.",
        )

    filename = audio.filename or f"q{pregunta}.webm"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
    blob_name = f"{familia_id}/{integrante_id}/q{pregunta}.{ext}"

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        gs_url = st.upload_to_gcs(tmp_path, st.GCS_BUCKET_AUDIOS, blob_name, audio.content_type or "audio/webm")
    finally:
        os.unlink(tmp_path)

    fs.save_respuesta(familia_id, integrante_id, pregunta, gs_url)
    fs.update_integrante_estado(familia_id, integrante_id, "en_progreso")
    return {"ok": True}


@app.post("/token/{token}/completar")
def token_completar(token: str):
    from pipeline.utils import firestore as fs
    match = fs.get_integrante_by_token(token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido o no encontrado")
    familia_id, integrante_id, _ = match
    fs.update_integrante_estado(familia_id, integrante_id, "completo")
    _check_y_trigger(familia_id)
    return {"ok": True}


# ─── Webhooks de pago ────────────────────────────────────────────────────────

def _generar_access_token_familia(familia_id: str) -> None:
    """Generate and persist access_token (UUID4, 90 days) for a familia."""
    from pipeline.utils import firestore as fs

    if not fs.get_familia(familia_id):
        logger.warning("[webhook] familia no encontrada: %s", familia_id)
        return

    token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=90)
    fs.set_access_token(familia_id, token, expires_at)
    logger.info("[webhook] access_token generado para familia=%s expira=%s", familia_id, expires_at.date())


@app.post("/webhook/stripe")
async def webhook_stripe(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    if secret and not _verify_stripe_signature(payload, sig_header, secret):
        raise HTTPException(status_code=400, detail="Firma de webhook inválida")

    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    if event.get("type") == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        familia_id = (
            session.get("metadata", {}).get("familia_id")
            or session.get("client_reference_id")
        )
        if familia_id:
            _generar_access_token_familia(familia_id)

    return {"ok": True}


def _handle_mp_payment(payment_id: str) -> None:
    """Fetch payment from MercadoPago API and generate access_token if approved."""
    mp_token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not mp_token:
        logger.warning("[webhook-mp] MP_ACCESS_TOKEN no configurado")
        return

    try:
        resp = httpx.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {mp_token}"},
            timeout=10,
        )
        if not resp.is_success:
            logger.warning("[webhook-mp] error al obtener pago %s: %s", payment_id, resp.status_code)
            return

        payment = resp.json()
        if payment.get("status") == "approved":
            familia_id = payment.get("external_reference")
            if familia_id:
                _generar_access_token_familia(familia_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[webhook-mp] excepción procesando pago %s: %s", payment_id, exc)


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):
    # MercadoPago sends both modern webhooks (JSON body) and legacy IPN (query params).
    topic = request.query_params.get("topic", "")
    payment_id_query = request.query_params.get("id", "")

    try:
        body = await request.json()
    except Exception:
        body = {}

    event_type = body.get("type", "") or topic
    payment_id = body.get("data", {}).get("id") or (payment_id_query if topic == "payment" else "")

    if event_type == "payment" and payment_id:
        _handle_mp_payment(str(payment_id))

    return {"ok": True}


# ─── Auth: magic link ─────────────────────────────────────────────────────────

@app.get("/auth/{token}")
def auth_magic_link(token: str):
    """Validate access_token, set session cookie, redirect to /mi-familia."""
    from pipeline.utils import firestore as fs

    result = fs.get_familia_by_access_token(token)
    if result is None:
        raise HTTPException(status_code=404, detail="Link inválido o expirado")

    familia_id, _ = result
    signed = _sign_session(familia_id)

    response = RedirectResponse(url="/mi-familia", status_code=303)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=signed,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_SESSION_MAX_AGE,
    )
    return response


class RequestLinkBody(BaseModel):
    email: str


_MAGIC_LINK_MAX_REQUESTS = 3
_MAGIC_LINK_WINDOW_SECONDS = 3600


@app.post("/auth/request-link")
@limiter.limit("20/hour")  # coarse IP-level guard
async def request_magic_link(request: Request, body: RequestLinkBody):
    """
    Send a magic link to the given email.
    Rate-limited to 3 requests/hour per email.
    Returns the same response regardless of whether the email exists.
    """
    from pipeline.utils import firestore as fs
    from pipeline.utils.email import send_magic_link

    email = body.email.strip().lower()
    _GENERIC_OK = {"ok": True, "message": "Si el email está registrado, recibirás el link en breve."}

    # Per-email rate limit (stored in Firestore)
    email_key = hashlib.sha256(email.encode()).hexdigest()[:32]
    if not fs.check_and_record_rate_limit(email_key, _MAGIC_LINK_MAX_REQUESTS, _MAGIC_LINK_WINDOW_SECONDS):
        return JSONResponse(content=_GENERIC_OK)

    try:
        result = fs.get_familia_by_email(email)
        if result:
            familia_id, familia = result
            token = fs.get_access_token(familia_id)
            if token:
                base = _recording_base()
                magic_link = f"{base}/auth/{token}"
                nombre_familia = familia.get("nombre", "tu familia")
                send_magic_link(email, nombre_familia, magic_link)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[request-link] error para %s: %s", email, exc)

    return _GENERIC_OK


# ─── Session: GET /me ─────────────────────────────────────────────────────────

@app.get("/me")
def get_me(request: Request):
    """Return familia data for the currently authenticated session."""
    from pipeline.utils import firestore as fs, storage as st

    cookie_value = request.cookies.get(_SESSION_COOKIE, "")
    if not cookie_value:
        raise HTTPException(status_code=401, detail="No autenticado")

    familia_id = _verify_session(cookie_value)
    if not familia_id:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")

    familia = fs.get_familia(familia_id)
    if not familia:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    integrantes = fs.get_integrantes(familia_id)
    base = _recording_base()
    tokens_estado = [
        {
            "nombre": i.get("nombre", ""),
            "estado": i.get("estado", "pendiente"),
            "link": f"{base}/r/{i.get('token_unico', '')}" if i.get("token_unico") else "",
            "email": i.get("email", ""),
            "token": i.get("token_unico", ""),
        }
        for i in integrantes
    ]

    # Derive book production status
    total = len(integrantes)
    completados = sum(1 for i in integrantes if i.get("estado") == "completo")
    familia_estado = familia.get("estado", "")
    if familia_estado in ("entregado",):
        libro_status = "listo"
    elif familia_estado in ("generando",) or (total > 0 and completados == total):
        libro_status = "produccion"
    else:
        libro_status = "esperando"

    # Signed URL for cover photo (24h)
    foto_portada_url = None
    gs_foto = familia.get("foto_portada_url", "")
    if gs_foto and gs_foto.startswith("gs://"):
        try:
            foto_portada_url = st.get_signed_url(gs_foto, expiration_hours=24)
        except Exception:
            pass

    # PDF signed URL if book is done (7 days)
    pdf_url = None
    if libro_status == "listo":
        gs_libro = familia.get("libro_url", "")
        if gs_libro and gs_libro.startswith("gs://"):
            try:
                pdf_url = st.get_signed_url(gs_libro, expiration_hours=168)
            except Exception:
                pass

    return {
        "familia_id": familia_id,
        "nombre_familia": familia.get("nombre", ""),
        "comprador": familia.get("comprador", {}),
        "tokens": tokens_estado,
        "estado": familia_estado,
        "libro_status": libro_status,
        "foto_portada_url": foto_portada_url,
        "pdf_url": pdf_url,
    }


# ─── Familia: link de acceso (usado por /gracias) ────────────────────────────

@app.get("/familia/{familia_id}/link-acceso")
def familia_link_acceso(familia_id: str):
    """Return the access link for a familia once payment is confirmed."""
    from pipeline.utils import firestore as fs

    token = fs.get_access_token(familia_id)
    if token is None:
        return {"disponible": False, "link": None}

    base = _recording_base()
    return {"disponible": True, "link": f"{base}/auth/{token}"}


# ─── Reenviar link de grabación ──────────────────────────────────────────────

class ReenviarInvitacionBody(BaseModel):
    token: str


@app.post("/familia/{familia_id}/reenviar-invitacion")
@limiter.limit("20/hour")
async def reenviar_invitacion(familia_id: str, request: Request, body: ReenviarInvitacionBody):
    """Reenvía el link de grabación por email al integrante identificado por su token."""
    from pipeline.utils import firestore as fs
    from pipeline.utils.email import send_recordatorio

    familia = fs.get_familia(familia_id)
    if not familia:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    match = fs.get_integrante_by_token(body.token)
    if match is None:
        raise HTTPException(status_code=404, detail="Token inválido")

    match_familia_id, _, integrante_data = match
    if match_familia_id != familia_id:
        raise HTTPException(status_code=403, detail="Token no pertenece a esta familia")

    email = integrante_data.get("email", "")
    if not email:
        raise HTTPException(status_code=422, detail="Este integrante no tiene email registrado")

    nombre = integrante_data.get("nombre", "")
    nombre_familia = familia.get("nombre", "")
    base = _recording_base()
    token_url = f"{base}/r/{body.token}"

    send_recordatorio(
        email_integrante=email,
        nombre_integrante=nombre,
        nombre_familia=nombre_familia,
        token_url=token_url,
    )

    return {"ok": True}


# ─── Admin ────────────────────────────────────────────────────────────────────

@app.post("/admin/test-email")
def test_email(_: None = Depends(_admin_auth)):
    from pipeline.utils.email import send_bienvenida

    send_bienvenida(
        email_comprador="test@raices.app",
        nombre_familia="Familia García",
        tokens=[
            {"nombre": "Abuela Rosa", "url": "https://example.com/grabar/abc123"},
            {"nombre": "Tío Carlos", "url": "https://example.com/grabar/def456"},
        ],
    )
    return {"ok": True, "message": "Email de prueba enviado"}
