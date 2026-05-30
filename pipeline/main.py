"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import os
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.utils import firestore as db
from pipeline.utils import storage

app = FastAPI(title="Familia Libro Pipeline", version="2.0")

BASE_URL = os.environ.get("SERVICE_URL", "https://familia-pipeline-776445604502.us-central1.run.app")
ADMIN_KEY = "familia-admin-2026"

_ONBOARDING_HTML = os.path.join(os.path.dirname(__file__), "..", "onboarding.html")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Onboarding UI ────────────────────────────────────────────────────────────

@app.get("/onboarding", response_class=FileResponse)
def onboarding_ui():
    return FileResponse(_ONBOARDING_HTML, media_type="text/html")


# ─── Onboarding ───────────────────────────────────────────────────────────────

class Integrante(BaseModel):
    nombre: str
    email: str = ""
    rol: str = ""
    fecha_nac: str = ""
    es_menor: bool = False
    pais: str = ""


class Relacion(BaseModel):
    persona_a: str
    relacion: str
    persona_b: str


class OnboardingRequest(BaseModel):
    familia_id: str
    nombre_familia: str
    email_comprador: str
    integrantes: list[Integrante]
    relaciones: list[Relacion] = []


@app.post("/onboarding")
def onboarding(req: OnboardingRequest):
    """
    Crea/actualiza la familia en Firestore, registra integrantes, relaciones
    y genera tokens únicos de grabación para cada integrante.
    """
    db.create_familia(
        familia_id=req.familia_id,
        nombre_familia=req.nombre_familia,
        email_comprador=req.email_comprador,
    )

    for ing in req.integrantes:
        db.seed_integrante(
            nombre=ing.nombre,
            fecha_nac=ing.fecha_nac,
            rol=ing.rol,
            es_menor=ing.es_menor,
            email=ing.email,
            pais=ing.pais,
            familia_id=req.familia_id,
        )

    for rel in req.relaciones:
        db.seed_relacion(
            persona_a=rel.persona_a,
            relacion=rel.relacion,
            persona_b=rel.persona_b,
            familia_id=req.familia_id,
        )

    tokens_result = []
    for ing in req.integrantes:
        token = db.generar_token(ing.nombre)
        db.create_token(
            familia_id=req.familia_id,
            token=token,
            nombre=ing.nombre,
            email=ing.email,
        )
        link = f"{BASE_URL}/r/{token}"
        tokens_result.append({"nombre": ing.nombre, "token": token, "link": link})

    return {"familia_id": req.familia_id, "estado": "onboarding", "tokens": tokens_result}


# ─── Foto de portada ──────────────────────────────────────────────────────────

BUCKET_FOTOS = os.environ.get("GCS_BUCKET_FOTOS", "libro-familiar-fotos")


@app.post("/familia/{familia_id}/foto-portada")
async def subir_foto_portada(familia_id: str, file: UploadFile = File(...)):
    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    try:
        from google.cloud import storage as gcs_storage

        content = await file.read()
        gcs_path = f"{familia_id}/portada.jpg"
        gcs_uri = f"gs://{BUCKET_FOTOS}/{gcs_path}"

        client = gcs_storage.Client()
        bucket = client.bucket(BUCKET_FOTOS)
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(content, content_type=file.content_type or "image/jpeg")

        db.update_familia_campo(familia_id, "foto_portada_url", gcs_uri)
        return {"url": gcs_uri}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error al subir foto: {exc}") from exc


# ─── Redirección por token ─────────────────────────────────────────────────────

@app.get("/r/{token}")
def redirigir_token(token: str):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    familia_id = doc.get("familia_id")
    if familia_id:
        db.marcar_token_usado(familia_id=familia_id, token=token)

    return RedirectResponse(url=BASE_URL, status_code=302)


# ─── Estado de familia ────────────────────────────────────────────────────────

@app.get("/familia/{familia_id}/estado")
def estado_familia(familia_id: str):
    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    integrantes = db.get_familia_integrantes(familia_id)
    con_transcripcion = 0
    con_capitulo = 0
    integrantes_detalle = []

    for ing in integrantes:
        nombre = ing["nombre"]
        transcripciones = db.get_transcripciones(nombre, familia_id)
        perfil = db.get_profile(nombre, familia_id)
        tiene_trans = len(transcripciones) > 0
        tiene_cap = bool(perfil and perfil.get("capitulo"))
        if tiene_trans:
            con_transcripcion += 1
        if tiene_cap:
            con_capitulo += 1
        integrantes_detalle.append({
            "nombre": nombre,
            "rol": ing.get("rol", ""),
            "es_menor": ing.get("es_menor", False),
            "tiene_transcripcion": tiene_trans,
            "tiene_capitulo": tiene_cap,
        })

    return {
        "familia_id": familia_id,
        "nombre_familia": familia.get("nombre", ""),
        "estado": familia.get("estado", ""),
        "total_integrantes": len(integrantes),
        "con_transcripcion": con_transcripcion,
        "con_capitulo": con_capitulo,
        "integrantes": integrantes_detalle,
    }


# ─── Trigger pipeline ─────────────────────────────────────────────────────────

def _run_pipeline_bg(familia_id: str, nombres: list[str], nombre_familia: str, pais: str):
    db.update_familia_estado(familia_id, "generando")
    try:
        orchestrator.run(nombres=nombres, pais=pais, familia=nombre_familia)
        db.update_familia_estado(familia_id, "entregado")
    except Exception:
        db.update_familia_estado(familia_id, "error")
        raise


@app.post("/familia/{familia_id}/trigger-pipeline")
def trigger_pipeline(
    familia_id: str,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(default=None),
):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado")

    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    integrantes = db.get_familia_integrantes(familia_id)
    nombres = [ing["nombre"] for ing in integrantes if not ing.get("es_menor")]

    if not nombres:
        raise HTTPException(status_code=422, detail="No hay integrantes adultos para procesar")

    nombre_familia = familia.get("nombre", familia_id)
    pais = familia.get("pais", "argentina")

    background_tasks.add_task(
        _run_pipeline_bg,
        familia_id=familia_id,
        nombres=nombres,
        nombre_familia=nombre_familia,
        pais=pais,
    )
    return {"status": "iniciado", "familia_id": familia_id, "nombres": nombres}


# ─── Full pipeline ────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nombres: list[str]
    pais: str = "argentina"
    solo_desde: str | None = None
    familia: str = "Familia Mariño · Saraniti"
    upload_to_gcs: bool = True


@app.post("/run/pipeline")
def run_pipeline(req: PipelineRequest):
    result = orchestrator.run(
        nombres=req.nombres,
        pais=req.pais,
        solo_desde=req.solo_desde,
        familia=req.familia,
        upload_to_gcs=req.upload_to_gcs,
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
    doc_ids: list[str] | None = None
    nombre: str | None = None
    pais: str = "argentina"
    solo_pendientes: bool = True


@app.post("/run/transcriber")
def run_transcriber(req: TranscriberRequest):
    return transcriber.run(
        doc_ids=req.doc_ids,
        pais=req.pais,
        nombre=req.nombre,
        solo_pendientes=req.solo_pendientes,
    )


# ─── Paso 2: Voice agent ──────────────────────────────────────────────────────

class NombresRequest(BaseModel):
    nombres: list[str]


@app.post("/run/voice")
def run_voice(req: NombresRequest):
    return voice_agent.run(req.nombres)


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
        p = db.get_profile(nombre)
        if not p:
            raise HTTPException(status_code=404, detail=f"Perfil no encontrado: {nombre}")
        personas_meta.append({
            "nombre": nombre,
            "fecha_nac": db.get_fecha_nac(nombre),
            "perfil_voz": p.get("perfil_voz", {}),
        })
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
    upload_to_gcs: bool = True


@app.post("/run/layout")
def run_layout(req: LayoutRequest):
    from pipeline.agents import editor_agent
    from datetime import datetime

    personas_meta = []
    capitulos = {}
    for nombre in req.nombres:
        p = db.get_profile(nombre)
        if not p:
            raise HTTPException(status_code=404, detail=f"Perfil no encontrado: {nombre}")
        personas_meta.append({
            "nombre": nombre,
            "fecha_nac": db.get_fecha_nac(nombre),
            "perfil_voz": p.get("perfil_voz", {}),
        })
        capitulos[nombre] = p.get("capitulo_revisado") or p.get("capitulo", "")

    manuscript = editor_agent.run(personas_meta, capitulos)
    pdf_path = layout_agent.run(
        manuscript=manuscript,
        personas_meta=personas_meta,
        nombre_familia=req.familia,
    )

    if req.upload_to_gcs:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"libro_{db.FAMILIA_ID}_{ts}.pdf"
        gcs_url = storage.upload_pdf(pdf_path, filename)
        return {"pdf": gcs_url, "uploaded": True}

    return {"pdf": pdf_path, "uploaded": False}
