"""
FastAPI entrypoint for the pipeline.
All heavy work happens in the agent modules.
"""

import os
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, UploadFile
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
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email_comprador,
            "subject": f"{nombre_integrante} completó su grabación",
            "html": (
                f"<p>¡Hola!</p>"
                f"<p><strong>{nombre_integrante}</strong> ya terminó de grabar su historia "
                f"para el <strong>{nombre_familia}</strong>.</p>"
                f"<p>Podés ver el progreso del resto en tu panel de seguimiento.</p>"
            ),
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
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email_comprador,
            "subject": f"¡Tu libro está listo! — {nombre_familia}",
            "html": (
                f"<p>¡Hola!</p>"
                f"<p>El <strong>{nombre_familia}</strong> ya está terminado. "
                f"Podés descargarlo en el link de abajo — válido por 30 días.</p>"
                f"<p><a href='{pdf_url}' style='font-size:16px;font-weight:bold'>→ Descargar el libro</a></p>"
                f"<p style='font-size:12px;color:#888'>Si el link venció, contactanos y te generamos uno nuevo.</p>"
            ),
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

    _enviar_email_progreso_html(
        email=email_comprador,
        nombre_familia=nombre_familia,
        completados=completados,
        pendientes=pendientes,
        total=total,
        percentil=percentil,
    )

    return {"ok": True, "email": email_comprador, "percentil": percentil}


def _enviar_email_progreso_html(
    email: str,
    nombre_familia: str,
    completados: list,
    pendientes: list,
    total: int,
    percentil: int,
):
    try:
        import resend  # type: ignore
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        if not resend.api_key:
            return

        n_comp = len(completados)
        pct    = round((n_comp / total) * 100) if total else 0

        # Rows de la tabla
        def fila_comp(t):
            return (
                f"<tr>"
                f"<td style='padding:10px 16px;border-bottom:1px solid #f0e8d8'>{t.get('nombre','')}</td>"
                f"<td style='padding:10px 16px;border-bottom:1px solid #f0e8d8;color:#3B6D11;font-weight:600'>✓ Completó</td>"
                f"</tr>"
            )

        def fila_pend(t):
            link = f"https://familia-pipeline-776445604502.us-central1.run.app/r/{t.get('token','')}"
            return (
                f"<tr>"
                f"<td style='padding:10px 16px;border-bottom:1px solid #f0e8d8'>{t.get('nombre','')}</td>"
                f"<td style='padding:10px 16px;border-bottom:1px solid #f0e8d8;color:#aaa'>"
                f"○ Pendiente &nbsp;<a href='{link}' style='font-size:11px;color:#8b5e3c'>Mandar link →</a>"
                f"</td>"
                f"</tr>"
            )

        filas = "".join(fila_comp(t) for t in completados) + "".join(fila_pend(t) for t in pendientes)

        stat_txt = ""
        if percentil >= 90:
            stat_txt = f"🏆 Están en el <strong>top {100-percentil}% de familias más rápidas</strong> respondiendo."
        elif percentil >= 70:
            stat_txt = f"⚡ Van más rápido que el <strong>{percentil}% de las familias</strong>. ¡Siguen bien!"
        elif percentil >= 40:
            stat_txt = f"📈 Van a buen ritmo — más rápido que el <strong>{percentil}%</strong> de las familias."
        else:
            stat_txt = f"💬 Todavía están a tiempo — la mayoría de las historias se graban en una semana."

        html = f"""
        <div style="font-family:'Georgia',serif;max-width:560px;margin:0 auto;color:#3d2b0a">
          <div style="background:#fdf8f2;border-bottom:2px solid #c8a96e;padding:32px 40px;text-align:center">
            <p style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#c8a96e;margin:0 0 8px">Libro de Memorias</p>
            <h1 style="font-size:26px;font-weight:400;margin:0">{nombre_familia}</h1>
          </div>

          <div style="background:#fff;padding:32px 40px">
            <p style="font-size:16px;margin:0 0 24px">
              Ya grabaron <strong>{n_comp} de {total}</strong> historias ({pct}% completado).
            </p>

            <!-- Barra de progreso -->
            <div style="background:#e8d9b8;border-radius:99px;height:8px;margin-bottom:28px">
              <div style="background:#c8a96e;height:8px;border-radius:99px;width:{pct}%"></div>
            </div>

            <!-- Stat -->
            <div style="background:#fdf6f0;border-left:3px solid #c8a96e;padding:14px 20px;margin-bottom:28px;font-size:14px">
              {stat_txt}
            </div>

            <!-- Tabla -->
            <table style="width:100%;border-collapse:collapse;font-size:14px">
              <thead>
                <tr style="background:#faf6f0">
                  <th style="padding:10px 16px;text-align:left;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#9a7b5a;font-weight:600">Integrante</th>
                  <th style="padding:10px 16px;text-align:left;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#9a7b5a;font-weight:600">Estado</th>
                </tr>
              </thead>
              <tbody>{filas}</tbody>
            </table>
          </div>

          <div style="background:#fdf8f2;padding:24px 40px;text-align:center;border-top:1px solid #e8d9b8">
            <p style="font-size:12px;color:#9a7b5a;margin:0">
              Este es un mail automático del Libro de Memorias Familiar.<br>
              Respondé este mail si necesitás ayuda.
            </p>
          </div>
        </div>
        """

        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"Progreso del {nombre_familia} — {len(completados)}/{total} grabaciones",
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
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "Libro Familiar <noreply@librofamiliar.com>"),
            "to": email,
            "subject": f"Todavía falta tu historia, {nombre} — {nombre_familia}",
            "html": (
                f"<p>Hola {nombre},</p>"
                f"<p>Te escribimos porque todavía no grabaste tu historia para el <strong>{nombre_familia}</strong>.</p>"
                f"<p>Son 16 preguntas cortas — podés hacerlo en una sola sentada o de a poco. "
                f"Tu historia queda guardada para siempre.</p>"
                f"<p><a href='{link}' style='font-size:16px;font-weight:bold'>→ Empezar a grabar</a></p>"
                f"<p style='font-size:12px;color:#888'>Si ya grabaste, ignorá este mensaje.</p>"
            ),
        })
    except Exception:
        pass


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
