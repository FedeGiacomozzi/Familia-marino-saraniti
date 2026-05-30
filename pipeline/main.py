"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import os
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
from pipeline.utils import firestore as db
from pipeline.utils import storage

app = FastAPI(title="Familia Libro Pipeline", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

BASE_URL = os.environ.get("SERVICE_URL", "https://familia-pipeline-776445604502.us-central1.run.app")
ADMIN_KEY = "familia-admin-2026"

_ONBOARDING_HTML  = os.path.join(os.path.dirname(__file__), "..", "onboarding.html")
_RECORDING_HTML   = os.path.join(os.path.dirname(__file__), "..", "recording.html")
_ADMIN_HTML       = os.path.join(os.path.dirname(__file__), "..", "admin.html")
_TEMPLATES_DIR    = os.path.join(os.path.dirname(__file__), "templates")


def _load_template(name: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, name)
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


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


# ─── Grabación: servir UI ─────────────────────────────────────────────────────

@app.get("/recording", response_class=FileResponse)
def recording_ui():
    return FileResponse(_RECORDING_HTML, media_type="text/html")


# ─── Redirección por token ─────────────────────────────────────────────────────

@app.get("/r/{token}")
def redirigir_token(token: str):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    familia_id = doc.get("familia_id")
    if familia_id:
        db.marcar_token_usado(familia_id=familia_id, token=token)

    return RedirectResponse(url=f"{BASE_URL}/recording?token={token}", status_code=302)


# ─── Token info (para recording.html) ─────────────────────────────────────────

@app.get("/token/{token}/info")
def token_info(token: str):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    return {
        "nombre": doc.get("nombre"),
        "familia_id": doc.get("familia_id"),
        "estado": doc.get("estado", "pendiente"),
    }


# ─── Subir foto del integrante (desde recording page) ─────────────────────────

@app.post("/token/{token}/foto")
async def subir_foto_integrante(token: str, foto: UploadFile = File(...)):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    familia_id = doc["familia_id"]
    nombre     = doc["nombre"]
    content    = await foto.read()
    ct         = foto.content_type or "image/jpeg"
    ext        = ct.split("/")[-1].split(";")[0] or "jpg"
    blob_name  = f"{familia_id}/{db._nombre_key(nombre)}/foto.{ext}"

    try:
        gcs_uri = storage.upload_audio_bytes(content, storage.FOTO_BUCKET, blob_name, ct)
        db.seed_respuesta(nombre=nombre, pregunta="foto", link_audio="", foto_url=gcs_uri, familia_id=familia_id)
        return {"ok": True, "url": gcs_uri}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─── Guardar respuesta de audio ────────────────────────────────────────────────

@app.post("/token/{token}/respuesta")
async def guardar_respuesta(
    token: str,
    pregunta: int = Form(...),
    audio: UploadFile = File(...),
):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    familia_id = doc["familia_id"]
    nombre     = doc["nombre"]

    content      = await audio.read()
    ct           = audio.content_type or "audio/webm"
    ext          = ct.split("/")[-1].split(";")[0] or "webm"
    blob_name    = f"{familia_id}/{db._nombre_key(nombre)}/q{pregunta}.{ext}"
    gcs_uri      = storage.upload_audio_bytes(content, storage.AUDIO_BUCKET, blob_name, ct)

    db.seed_respuesta(nombre=nombre, pregunta=pregunta, link_audio=gcs_uri, familia_id=familia_id)

    if doc.get("estado", "pendiente") == "pendiente":
        db.update_token_estado(familia_id, token, "en_progreso")

    return {"ok": True}


# ─── Completar grabación ───────────────────────────────────────────────────────

@app.post("/token/{token}/completar")
async def completar_token(token: str):
    doc = db.get_token(token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    familia_id = doc["familia_id"]
    nombre     = doc["nombre"]
    db.update_token_estado(familia_id, token, "completado")

    familia        = db.get_familia(familia_id)
    email_comprador = (familia or {}).get("comprador", {}).get("email", "")
    nombre_familia  = (familia or {}).get("nombre", familia_id)

    if email_comprador:
        _enviar_email_completado(nombre, email_comprador, nombre_familia)

    return {"ok": True}


def _enviar_email_completado(nombre_integrante: str, email_comprador: str, nombre_familia: str):
    try:
        import resend  # type: ignore
        from datetime import datetime
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        now = datetime.utcnow().strftime("%-d de %B, %Y a las %H:%M")
        dashboard_url = f"{BASE_URL}/onboarding"
        tpl = _load_template("email_completado.html")
        html = (tpl
            .replace("{{COMPRADOR_NOMBRE}}", email_comprador.split("@")[0].capitalize())
            .replace("{{INTEGRANTE_NOMBRE}}", nombre_integrante)
            .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
            .replace("{{FECHA_HORA}}", now)
            .replace("{{DASHBOARD_URL}}", dashboard_url)
        ) if tpl else f"<p>{nombre_integrante} completó su grabación para {nombre_familia}.</p>"
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email_comprador,
            "subject": f"{nombre_integrante} completó su grabación — {nombre_familia}",
            "html": html,
        })
    except Exception:
        pass


# ─── Estado de tokens por familia (para dashboard) ────────────────────────────

@app.get("/familia/{familia_id}/tokens-estado")
def tokens_estado(familia_id: str):
    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")
    tokens = db.get_tokens_familia(familia_id)
    return {
        "familia_id": familia_id,
        "nombre_familia": familia.get("nombre", ""),
        "tokens": [
            {
                "nombre": t.get("nombre", ""),
                "token":  t.get("token", ""),
                "link":   f"{BASE_URL}/r/{t.get('token','')}",
                "estado": t.get("estado", "pendiente"),
                "usado":  t.get("usado", False),
            }
            for t in tokens
        ],
    }


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


# ─── PDF entrega ─────────────────────────────────────────────────────────────

@app.get("/familia/{familia_id}/pdf")
def get_pdf_link(
    familia_id: str,
    x_admin_key: Optional[str] = Header(default=None),
):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")
    pdf_uri = familia.get("pdf_url")
    if not pdf_uri:
        raise HTTPException(status_code=404, detail="PDF no disponible aún")
    url_firmada = storage.generar_url_firmada(pdf_uri)
    db.update_familia_campo(familia_id, "pdf_url_firmada", url_firmada)
    return {"pdf_url": url_firmada, "expires_in_days": 30}


# ─── Admin panel ─────────────────────────────────────────────────────────────

@app.get("/admin", response_class=FileResponse)
def admin_ui():
    return FileResponse(_ADMIN_HTML, media_type="text/html")


@app.get("/admin/familias")
def admin_familias(x_admin_key: Optional[str] = Header(default=None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    familias = db.get_all_familias()
    result = []
    for f in familias:
        fid     = f["_id"]
        tokens  = db.get_tokens_familia(fid)
        total   = len(tokens)
        completos = sum(1 for t in tokens if t.get("estado") == "completado")
        result.append({
            "familia_id":    fid,
            "nombre":        f.get("nombre", fid),
            "estado":        f.get("estado", ""),
            "email_comprador": f.get("comprador", {}).get("email", ""),
            "fecha_creacion": f.get("fecha_creacion", ""),
            "total_tokens":  total,
            "completados":   completos,
        })
    result.sort(key=lambda x: x.get("fecha_creacion", ""), reverse=True)
    return {"familias": result}


# ─── Trigger pipeline ─────────────────────────────────────────────────────────

def _run_pipeline_bg(familia_id: str, nombres: list[str], nombre_familia: str, pais: str):
    db.update_familia_estado(familia_id, "generando")
    try:
        result = orchestrator.run(nombres=nombres, pais=pais, familia=nombre_familia)
        db.update_familia_estado(familia_id, "entregado")

        # Guardar URL del PDF y enviar email de entrega
        if result.layout:
            try:
                pdf_url_firmada = storage.generar_url_firmada(result.layout)
                db.update_familia_campo(familia_id, "pdf_url", result.layout)
                db.update_familia_campo(familia_id, "pdf_url_firmada", pdf_url_firmada)
                familia = db.get_familia(familia_id)
                email_comprador = (familia or {}).get("comprador", {}).get("email", "")
                if email_comprador:
                    _enviar_email_entrega(email_comprador, nombre_familia, pdf_url_firmada)
            except Exception:
                pass  # No fallar el pipeline por el mail
    except Exception:
        db.update_familia_estado(familia_id, "error")
        raise


def _enviar_email_entrega(email_comprador: str, nombre_familia: str, pdf_url: str):
    try:
        import resend  # type: ignore
        from datetime import datetime
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        tokens = db.get_tokens_familia_by_email(email_comprador) if hasattr(db, "get_tokens_familia_by_email") else []
        total = len(tokens) if tokens else "varias"
        fecha = datetime.utcnow().strftime("%-d de %B, %Y")
        año = datetime.utcnow().year
        tpl = _load_template("email_entrega.html")
        html = (tpl
            .replace("{{COMPRADOR_NOMBRE}}", email_comprador.split("@")[0].capitalize())
            .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
            .replace("{{PDF_URL}}", pdf_url)
            .replace("{{DASHBOARD_URL}}", BASE_URL)
            .replace("{{TOTAL_HISTORIAS}}", str(total))
            .replace("{{FECHA_GENERACION}}", fecha)
            .replace("{{AÑO}}", str(año))
        ) if tpl else f"<p>Tu libro <strong>{nombre_familia}</strong> está listo. <a href='{pdf_url}'>Descargar PDF</a></p>"
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email_comprador,
            "subject": f"El libro de la {nombre_familia} está listo ↓",
            "html": html,
        })
    except Exception:
        pass


# ─── Email de progreso al comprador ──────────────────────────────────────────

@app.post("/familia/{familia_id}/email-progreso")
def enviar_email_progreso(
    familia_id: str,
    x_admin_key: Optional[str] = Header(default=None),
):
    """
    Manda al comprador un resumen del progreso: quién completó, quién falta,
    y una stat de posición relativa entre todas las familias.
    """
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado")

    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    email_comprador = familia.get("comprador", {}).get("email", "")
    if not email_comprador:
        raise HTTPException(status_code=422, detail="El comprador no tiene email registrado")

    nombre_familia = familia.get("nombre", familia_id)
    tokens = db.get_tokens_familia(familia_id)

    completados = [t for t in tokens if t.get("estado") == "completado"]
    pendientes  = [t for t in tokens if t.get("estado") != "completado"]
    total       = len(tokens)
    n_comp      = len(completados)

    # Stat relativa: % de familias con igual o menor tasa de completado
    todas = db.get_all_familias()
    tasas = []
    for f in todas:
        fid = f["_id"]
        if fid == familia_id:
            continue
        tkns = db.get_tokens_familia(fid)
        if not tkns:
            continue
        tasa = sum(1 for t in tkns if t.get("estado") == "completado") / len(tkns)
        tasas.append(tasa)

    tasa_propia = n_comp / total if total else 0
    if tasas:
        mejor_que = sum(1 for t in tasas if tasa_propia > t)
        percentil = round((mejor_que / len(tasas)) * 100)
    else:
        percentil = 100

    comprador_nombre = familia.get("comprador", {}).get("nombre", "")
    _enviar_email_progreso_html(
        email=email_comprador,
        nombre_familia=nombre_familia,
        completados=completados,
        pendientes=pendientes,
        total=total,
        percentil=percentil,
        comprador_nombre=comprador_nombre,
    )

    return {"ok": True, "email": email_comprador, "percentil": percentil}


def _enviar_email_progreso_html(
    email: str,
    nombre_familia: str,
    completados: list,
    pendientes: list,
    total: int,
    percentil: int,
    comprador_nombre: str = "",
):
    try:
        import resend  # type: ignore
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return

        n_comp = len(completados)
        pct    = round((n_comp / total) * 100) if total else 0

        def fila_comp(t):
            return (
                f"<tr>"
                f"<td style='padding:10px 0;border-bottom:0.5px solid #e8d9b8;color:#3d2b0a'>{t.get('nombre','')}</td>"
                f"<td style='padding:10px 0;border-bottom:0.5px solid #e8d9b8;text-align:right'>"
                f"<span style='background:#EAF3DE;color:#3B6D11;font-size:11px;padding:3px 10px;border-radius:100px;font-weight:700'>✓ Completó</span>"
                f"</td>"
                f"</tr>"
            )

        def fila_pend(t):
            return (
                f"<tr>"
                f"<td style='padding:10px 0;border-bottom:0.5px solid #e8d9b8;color:#3d2b0a'>{t.get('nombre','')}</td>"
                f"<td style='padding:10px 0;border-bottom:0.5px solid #e8d9b8;text-align:right'>"
                f"<span style='background:#FFF3E0;color:#8B5E10;font-size:11px;padding:3px 10px;border-radius:100px'>Pendiente</span>"
                f"</td>"
                f"</tr>"
            )

        filas = "".join(fila_comp(t) for t in completados) + "".join(fila_pend(t) for t in pendientes)

        if percentil >= 90:
            stat_titulo = f"Top {100-percentil}% de familias más rápidas"
            stat_sub = "Están grabando a un ritmo excepcional."
        elif percentil >= 70:
            stat_titulo = f"Más rápidos que el {percentil}% de las familias"
            stat_sub = "Van muy bien — siguen así."
        elif percentil >= 40:
            stat_titulo = f"Más rápidos que el {percentil}% de las familias"
            stat_sub = "Van a buen ritmo."
        else:
            stat_titulo = "Todavía están a tiempo"
            stat_sub = "La mayoría de las historias se graban en la primera semana."

        nombre_display = comprador_nombre or email.split("@")[0].capitalize()

        tpl = _load_template("email_progreso.html")
        html = (tpl
            .replace("{{COMPRADOR_NOMBRE}}", nombre_display)
            .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
            .replace("{{PCT}}", str(pct))
            .replace("{{COMPLETADOS}}", str(n_comp))
            .replace("{{TOTAL}}", str(total))
            .replace("{{STAT_TITULO}}", stat_titulo)
            .replace("{{STAT_SUBTITULO}}", stat_sub)
            .replace("{{FILAS_TABLA}}", filas)
            .replace("{{DASHBOARD_URL}}", BASE_URL)
        ) if tpl else f"<p>Progreso de {nombre_familia}: {n_comp}/{total} grabaciones completadas.</p>"

        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"Actualización de la {nombre_familia} — {n_comp}/{total} grabaciones",
            "html": html,
        })
    except Exception:
        pass


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


# ─── Reminder de grabación ────────────────────────────────────────────────────

@app.post("/familia/{familia_id}/reminder")
def enviar_reminder(
    familia_id: str,
    x_admin_key: Optional[str] = Header(default=None),
):
    """
    Envía email de recordatorio a los integrantes con token no completado
    que tengan email registrado.
    """
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado")

    familia = db.get_familia(familia_id)
    if familia is None:
        raise HTTPException(status_code=404, detail="Familia no encontrada")

    tokens = db.get_tokens_familia(familia_id)
    nombre_familia = familia.get("nombre", familia_id)
    enviados = []

    for t in tokens:
        if t.get("estado") == "completado":
            continue
        email = t.get("email", "").strip()
        if not email:
            continue
        link = f"{BASE_URL}/r/{t['token']}"
        _enviar_email_reminder(
            email=email,
            nombre=t.get("nombre", ""),
            nombre_familia=nombre_familia,
            link=link,
        )
        enviados.append({"nombre": t.get("nombre"), "email": email})

    return {"enviados": enviados, "total": len(enviados)}


def _enviar_email_reminder(email: str, nombre: str, nombre_familia: str, link: str):
    try:
        import resend  # type: ignore
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        tpl = _load_template("email_reminder.html")
        html = (tpl
            .replace("{{NOMBRE}}", nombre)
            .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
            .replace("{{RECORDING_URL}}", link)
        ) if tpl else f"<p>Hola {nombre}, todavía falta tu historia. <a href='{link}'>Empezar a grabar</a></p>"
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"{nombre}, tu historia todavía no está escrita — {nombre_familia}",
            "html": html,
        })
    except Exception:
        pass


# ─── Mi Familia (comprador dashboard) ───────────────────────────────────────
#
# Flujo magic link:
#   1. Comprador va a /mi-familia y escribe su email
#   2. POST /auth/magic-link genera un token de 1 hora y manda email
#   3. Email contiene link a /mi-familia?ml=TOKEN
#   4. GET /auth/magic-link/{token}/familia verifica token y devuelve datos
#   5. POST /auth/magic-link/{token}/reenviar-links reenvía links de grabación

_MI_FAMILIA_HTML = os.path.join(os.path.dirname(__file__), "..", "mi-familia.html")

import secrets as _secrets_mod
from datetime import datetime, timedelta


@app.get("/mi-familia", response_class=FileResponse)
def mi_familia_ui():
    return FileResponse(_MI_FAMILIA_HTML, media_type="text/html")


class MagicLinkRequest(BaseModel):
    email: str


@app.post("/auth/magic-link")
def solicitar_magic_link(req: MagicLinkRequest, background_tasks: BackgroundTasks):
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Email inválido")

    # Buscar familias con este email de comprador
    familias = db.get_all_familias()
    matching = [f for f in familias if f.get("comprador", {}).get("email", "").lower() == email]
    if not matching:
        # Responder igual para no revelar si el email existe
        return {"ok": True}

    # Generar magic token para cada familia (si hay múltiples, el último)
    familia = matching[-1]
    familia_id = familia["_id"]
    ml_token = _secrets_mod.token_urlsafe(32)
    expira = (datetime.utcnow() + timedelta(hours=1)).isoformat()

    db.update_familia_campo(familia_id, "magic_link", {
        "token": ml_token,
        "expira": expira,
        "email": email,
    })

    link = f"{BASE_URL}/mi-familia?ml={ml_token}"
    nombre = familia.get("comprador", {}).get("nombre", "")
    background_tasks.add_task(_enviar_magic_link, email, nombre, link, familia.get("nombre", ""))
    return {"ok": True}


def _enviar_magic_link(email: str, nombre: str, link: str, nombre_familia: str):
    try:
        import resend  # type: ignore
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        html = f"""
        <div style="font-family:'Georgia',serif;max-width:480px;margin:0 auto;color:#3d2b0a;background:#fff;border:1px solid #e8d9b8;border-radius:12px;overflow:hidden">
          <div style="background:#2C1A0E;padding:24px;text-align:center">
            <p style="font-family:'Playfair Display',serif;font-size:13px;letter-spacing:0.18em;color:#C4956A;text-transform:uppercase;margin:0 0 4px">Raíces</p>
            <h1 style="font-family:'Playfair Display',serif;font-size:20px;color:#F5EDD8;font-weight:400;margin:0">Tu acceso a {nombre_familia}</h1>
          </div>
          <div style="padding:28px">
            <p>Hola{' ' + nombre if nombre else ''}. Hacé click en el botón para acceder al estado de tu libro:</p>
            <div style="text-align:center;margin:24px 0">
              <a href="{link}" style="background:#2C1A0E;color:#F5EDD8;padding:14px 32px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:700;letter-spacing:0.04em">Ver mi libro →</a>
            </div>
            <p style="font-size:12px;color:#9a7b5a">Este link es válido por 1 hora. Si no pediste este email, podés ignorarlo.</p>
          </div>
        </div>
        """
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Raíces <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"Tu acceso a {nombre_familia} — Raíces",
            "html": html,
        })
    except Exception:
        pass


@app.get("/auth/magic-link/{ml_token}/familia")
def get_familia_por_magic_link(ml_token: str):
    familias = db.get_all_familias()
    familia = None
    for f in familias:
        ml = f.get("magic_link", {})
        if ml.get("token") == ml_token:
            expira = ml.get("expira", "")
            if expira and datetime.utcnow().isoformat() > expira:
                raise HTTPException(status_code=401, detail="El link expiró. Solicitá uno nuevo en /mi-familia")
            familia = f
            break

    if not familia:
        raise HTTPException(status_code=404, detail="Link inválido o expirado")

    familia_id = familia["_id"]
    tokens = db.get_tokens_familia(familia_id)
    pdf_url = familia.get("pdf_url_firmada") or familia.get("pdf_url")

    return {
        "familia_id": familia_id,
        "nombre_familia": familia.get("nombre", ""),
        "estado": familia.get("estado", ""),
        "pdf_url": pdf_url,
        "tokens": [
            {
                "nombre": t.get("nombre", ""),
                "estado": t.get("estado", "pendiente"),
                "link": f"{BASE_URL}/r/{t.get('token', '')}",
                "email": t.get("email", ""),
            }
            for t in tokens
        ],
    }


@app.post("/auth/magic-link/{ml_token}/reenviar-links")
def reenviar_links_magic(ml_token: str, background_tasks: BackgroundTasks):
    familias = db.get_all_familias()
    familia = None
    for f in familias:
        ml = f.get("magic_link", {})
        if ml.get("token") == ml_token:
            if datetime.utcnow().isoformat() > ml.get("expira", ""):
                raise HTTPException(status_code=401, detail="Link expirado")
            familia = f
            break

    if not familia:
        raise HTTPException(status_code=404, detail="Link inválido")

    familia_id = familia["_id"]
    nombre_familia = familia.get("nombre", "")
    tokens = db.get_tokens_familia(familia_id)

    for t in tokens:
        if t.get("estado") != "completado" and t.get("email"):
            link = f"{BASE_URL}/r/{t['token']}"
            background_tasks.add_task(
                _enviar_email_reminder,
                t["email"], t["nombre"], nombre_familia, link
            )
    return {"ok": True}


# ─── Mercado Pago webhook ────────────────────────────────────────────────────
#
# Flujo:
#   1. Comprador paga en MP → MP llama a POST /pago/mp-webhook
#   2. Verificamos el pago contra la API de MP
#   3. Extraemos metadata (nombre_familia, email_comprador, integrantes)
#   4. Creamos la familia en Firestore + generamos tokens
#   5. Enviamos email de bienvenida con los links a cada integrante
#
# Variables de entorno requeridas:
#   MP_ACCESS_TOKEN   — token de producción/sandbox de Mercado Pago
#   MP_WEBHOOK_SECRET — secret para validar la firma X-Signature (opcional)

import hashlib
import hmac
import urllib.request


def _mp_get_payment(payment_id: str) -> dict | None:
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        return None
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json
            return _json.loads(resp.read())
    except Exception:
        return None


def _mp_verify_signature(request_body: bytes, x_signature: str, x_request_id: str) -> bool:
    secret = os.environ.get("MP_WEBHOOK_SECRET", "")
    if not secret:
        return True  # sin secret configurado, aceptar todo (sandbox)
    try:
        parts = {p.split("=")[0]: p.split("=")[1] for p in x_signature.split(",")}
        ts  = parts.get("ts", "")
        v1  = parts.get("v1", "")
        manifest = f"id:{x_request_id};request-id:{x_request_id};ts:{ts};"
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, v1)
    except Exception:
        return False


def _procesar_pago_aprobado(payment_data: dict, background_tasks: BackgroundTasks):
    """
    Extrae datos del pago y crea la familia + tokens.
    metadata esperada en el pago MP:
      { nombre_familia, email_comprador, nombre_comprador,
        integrantes: "[{nombre, email}, ...]"  (JSON string) }
    """
    import json as _json

    status = payment_data.get("status", "")
    if status != "approved":
        return

    metadata    = payment_data.get("metadata", {})
    payment_id  = str(payment_data.get("id", ""))
    orden_id    = str(payment_data.get("order", {}).get("id", payment_id))
    nombre_fam  = metadata.get("nombre_familia", f"Familia {orden_id}")
    email_comp  = (
        metadata.get("email_comprador")
        or payment_data.get("payer", {}).get("email", "")
    )
    nombre_comp = (
        metadata.get("nombre_comprador")
        or payment_data.get("payer", {}).get("first_name", "")
    )
    familia_id  = f"mp-{payment_id}"

    # Integrantes: pueden venir como JSON string en metadata
    raw_integ = metadata.get("integrantes", "[]")
    try:
        integrantes_raw = _json.loads(raw_integ) if isinstance(raw_integ, str) else raw_integ
    except Exception:
        integrantes_raw = []

    if not integrantes_raw:
        # Sin integrantes en metadata → solo registrar el comprador
        integrantes_raw = [{"nombre": nombre_comp or "Comprador", "email": email_comp}]

    # Crear familia
    db.create_familia(
        familia_id=familia_id,
        nombre_familia=nombre_fam,
        email_comprador=email_comp,
    )
    # Guardar nombre del comprador
    db.update_familia_campo(familia_id, "comprador", {
        "email": email_comp,
        "nombre": nombre_comp,
        "payment_id": payment_id,
    })

    # Crear tokens + enviar emails
    tokens_result = []
    for ing in integrantes_raw:
        nombre_ing = ing.get("nombre", "").strip()
        email_ing  = ing.get("email", "").strip()
        if not nombre_ing:
            continue
        token = db.generar_token(nombre_ing)
        db.create_token(
            familia_id=familia_id,
            token=token,
            nombre=nombre_ing,
            email=email_ing,
        )
        db.seed_integrante(nombre=nombre_ing, email=email_ing, familia_id=familia_id)
        link = f"{BASE_URL}/r/{token}"
        tokens_result.append({"nombre": nombre_ing, "token": token, "link": link, "email": email_ing})

    # Enviar email de bienvenida al comprador + links a integrantes
    background_tasks.add_task(
        _enviar_email_bienvenida,
        email_comp, nombre_comp, nombre_fam, tokens_result
    )
    for t in tokens_result:
        if t["email"] and t["email"] != email_comp:
            background_tasks.add_task(
                _enviar_email_reminder,
                t["email"], t["nombre"], nombre_fam, t["link"]
            )


def _enviar_email_bienvenida(email: str, nombre: str, nombre_familia: str, tokens: list):
    try:
        import resend  # type: ignore
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        links_html = "".join(
            f"<li style='padding:6px 0'><strong>{t['nombre']}</strong> → "
            f"<a href='{t['link']}' style='color:#8B5E3C'>{t['link']}</a></li>"
            for t in tokens
        )
        html = f"""
        <div style="font-family:'Georgia',serif;max-width:560px;margin:0 auto;color:#3d2b0a;background:#fff;border:1px solid #e8d9b8;border-radius:12px;overflow:hidden">
          <div style="background:#2C1A0E;padding:32px;text-align:center">
            <p style="font-family:'Playfair Display',serif;font-size:13px;letter-spacing:0.18em;color:#C4956A;text-transform:uppercase;margin:0 0 8px">Raíces</p>
            <h1 style="font-family:'Playfair Display',serif;font-size:22px;color:#F5EDD8;font-weight:400;margin:0">Tu libro de familia<br><em>está en marcha</em></h1>
          </div>
          <div style="padding:32px">
            <p>Hola, {nombre or 'te damos la bienvenida'}. Recibimos tu compra del libro de la <strong>{nombre_familia}</strong>.</p>
            <p>Cada integrante tiene su link único de grabación. Podés compartirlos directamente:</p>
            <ul style="list-style:none;padding:0;margin:16px 0;font-size:14px">{links_html}</ul>
            <p style="font-size:14px;color:#9a7b5a">Cuando todos terminen de grabar, el libro se genera automáticamente y te avisamos por email.</p>
          </div>
          <div style="padding:16px 32px;border-top:1px solid #e8d9b8;font-size:12px;color:#9a7b5a">
            Raíces · Libros biográficos
          </div>
        </div>
        """
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Raíces <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"¡Tu libro está en marcha! — {nombre_familia}",
            "html": html,
        })
    except Exception:
        pass


@app.post("/pago/mp-webhook")
async def mp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str = Header(default=""),
    x_request_id: str = Header(default=""),
):
    body = await request.body()

    if x_signature and not _mp_verify_signature(body, x_signature, x_request_id):
        raise HTTPException(status_code=401, detail="Firma inválida")

    import json as _json
    try:
        data = _json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    action = data.get("action", "")
    topic  = data.get("type", data.get("topic", ""))

    if topic == "payment" and action in ("payment.created", "payment.updated"):
        payment_id = str(data.get("data", {}).get("id", "") or data.get("id", ""))
        if payment_id:
            payment_data = _mp_get_payment(payment_id)
            if payment_data:
                background_tasks.add_task(_procesar_pago_aprobado, payment_data, background_tasks)

    # MP espera 200 inmediato
    return {"received": True}


@app.get("/pago/mp-webhook")
def mp_webhook_verify():
    """MP verifica el endpoint con un GET antes de registrarlo."""
    return {"status": "ok"}


# ─── Stripe webhook ──────────────────────────────────────────────────────────
#
# Variables de entorno requeridas:
#   STRIPE_SECRET_KEY        — sk_live_... o sk_test_...
#   STRIPE_WEBHOOK_SECRET    — whsec_... (desde Stripe Dashboard > Webhooks)
#
# El checkout de Stripe lo maneja el front con Stripe Payment Links o Stripe.js.
# Cuando el pago se completa, Stripe llama a POST /pago/stripe-webhook.
# metadata del Payment Link debe incluir: nombre_familia, email_comprador,
# nombre_comprador, integrantes (JSON string).

@app.post("/pago/stripe-webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
):
    import json as _json

    body = await request.body()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")

    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe no configurado")

    # Verificar firma de Stripe
    if secret:
        try:
            import stripe as _stripe  # type: ignore
            _stripe.api_key = stripe_key
            event = _stripe.Webhook.construct_event(body, stripe_signature, secret)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Firma inválida: {exc}") from exc
    else:
        try:
            event = _json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="JSON inválido")

    event_type = event.get("type", "")

    if event_type == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        metadata = session.get("metadata", {})
        email_comp  = session.get("customer_details", {}).get("email", metadata.get("email_comprador", ""))
        nombre_comp = session.get("customer_details", {}).get("name", metadata.get("nombre_comprador", ""))
        nombre_fam  = metadata.get("nombre_familia", "Familia")
        payment_id  = session.get("payment_intent", session.get("id", ""))
        familia_id  = f"stripe-{payment_id}"

        raw_integ = metadata.get("integrantes", "[]")
        try:
            integrantes_raw = _json.loads(raw_integ) if isinstance(raw_integ, str) else raw_integ
        except Exception:
            integrantes_raw = [{"nombre": nombre_comp or "Comprador", "email": email_comp}]

        db.create_familia(familia_id=familia_id, nombre_familia=nombre_fam, email_comprador=email_comp)
        db.update_familia_campo(familia_id, "comprador", {
            "email": email_comp, "nombre": nombre_comp, "payment_id": payment_id, "gateway": "stripe"
        })

        tokens_result = []
        for ing in integrantes_raw:
            nombre_ing = ing.get("nombre", "").strip()
            email_ing  = ing.get("email", "").strip()
            if not nombre_ing:
                continue
            token = db.generar_token(nombre_ing)
            db.create_token(familia_id=familia_id, token=token, nombre=nombre_ing, email=email_ing)
            db.seed_integrante(nombre=nombre_ing, email=email_ing, familia_id=familia_id)
            tokens_result.append({"nombre": nombre_ing, "token": token,
                                   "link": f"{BASE_URL}/r/{token}", "email": email_ing})

        background_tasks.add_task(_enviar_email_bienvenida, email_comp, nombre_comp, nombre_fam, tokens_result)
        for t in tokens_result:
            if t["email"] and t["email"] != email_comp:
                background_tasks.add_task(_enviar_email_reminder, t["email"], t["nombre"], nombre_fam, t["link"])

    return {"received": True}


@app.post("/pago/crear-stripe-checkout")
def crear_stripe_checkout(req: CheckoutRequest):
    """
    Crea una Stripe Checkout Session y devuelve la URL de pago.
    Requiere stripe>=7.0.0 en requirements.txt.
    """
    import json as _json
    import stripe as _stripe  # type: ignore

    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe no configurado")

    _stripe.api_key = stripe_key

    PRECIOS_STRIPE = {
        "base": 7900,       # centavos USD
        "familiar": 9900,
        "extendido": 14900,
    }
    precio = PRECIOS_STRIPE.get(req.pack, 7900)
    integrantes_extra = max(0, len(req.integrantes) - 4)
    precio += integrantes_extra * 800

    try:
        session = _stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Libro biográfico — {req.nombre_familia}",
                        "description": f"{len(req.integrantes)} integrantes · Entrega digital PDF",
                    },
                    "unit_amount": precio,
                },
                "quantity": 1,
            }],
            mode="payment",
            customer_email=req.email_comprador,
            metadata={
                "nombre_familia": req.nombre_familia,
                "email_comprador": req.email_comprador,
                "nombre_comprador": req.nombre_comprador,
                "integrantes": _json.dumps(req.integrantes),
            },
            success_url=f"{BASE_URL}/pago/exito?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/#precios",
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error Stripe: {exc}") from exc


# ─── Crear checkout de Mercado Pago ──────────────────────────────────────────

class CheckoutRequest(BaseModel):
    nombre_familia: str
    email_comprador: str
    nombre_comprador: str
    integrantes: list[dict]  # [{nombre, email}]
    pack: str = "base"


@app.post("/pago/crear-checkout")
def crear_checkout(req: CheckoutRequest):
    """
    Crea una preferencia de pago en MP y devuelve el init_point (URL de pago).
    El front hace POST acá, recibe la URL y redirige al usuario a MP.
    """
    import json as _json
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Pasarela de pago no configurada")

    PRECIOS = {
        "base": 79,       # USD 79 — hasta 4 integrantes
        "familiar": 99,   # USD 99 — hasta 6 integrantes
        "extendido": 149, # USD 149 — hasta 10 integrantes
    }
    precio = PRECIOS.get(req.pack, 79)
    integrantes_extra = max(0, len(req.integrantes) - 4)
    precio += integrantes_extra * 8

    preference_payload = _json.dumps({
        "items": [{
            "title": f"Libro biográfico — {req.nombre_familia}",
            "quantity": 1,
            "unit_price": precio,
            "currency_id": "USD",
        }],
        "payer": {
            "email": req.email_comprador,
            "name": req.nombre_comprador,
        },
        "metadata": {
            "nombre_familia": req.nombre_familia,
            "email_comprador": req.email_comprador,
            "nombre_comprador": req.nombre_comprador,
            "integrantes": _json.dumps(req.integrantes),
        },
        "back_urls": {
            "success": f"{BASE_URL}/pago/exito",
            "failure": f"{BASE_URL}/pago/error",
            "pending": f"{BASE_URL}/pago/pendiente",
        },
        "auto_return": "approved",
        "notification_url": f"{BASE_URL}/pago/mp-webhook",
    }).encode()

    mp_req = urllib.request.Request(
        "https://api.mercadopago.com/checkout/preferences",
        data=preference_payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(mp_req, timeout=15) as resp:
            result = _json.loads(resp.read())
        return {
            "init_point": result.get("init_point"),
            "sandbox_init_point": result.get("sandbox_init_point"),
            "preference_id": result.get("id"),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error MP: {exc}") from exc


@app.get("/pago/exito")
def pago_exito():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "..", "landing.html"),
        media_type="text/html"
    )


@app.get("/pago/error")
def pago_error():
    return {"error": "El pago no fue procesado. Intentá de nuevo o contactanos."}


@app.get("/pago/pendiente")
def pago_pendiente():
    return {"mensaje": "Tu pago está siendo procesado. Te avisamos cuando esté confirmado."}


# ─── Landing ─────────────────────────────────────────────────────────────────

_LANDING_HTML = os.path.join(os.path.dirname(__file__), "..", "landing.html")


@app.get("/", response_class=FileResponse)
def landing():
    return FileResponse(_LANDING_HTML, media_type="text/html")


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
