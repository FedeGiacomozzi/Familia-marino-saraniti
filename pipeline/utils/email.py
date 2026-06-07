import logging
import os
from datetime import datetime
from pathlib import Path

import resend

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_FROM = os.environ.get("RESEND_FROM_EMAIL", "Raíces <noreply@raices.app>")


def _get_key() -> str | None:
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY no configurada")
        return None
    return RESEND_API_KEY


def send_bienvenida(email_comprador: str, nombre_familia: str, tokens: list[dict]) -> None:
    key = _get_key()
    if not key:
        return

    html = (_TEMPLATES_DIR / "email_completado.html").read_text()

    tokens_html = "".join(
        f'<a href="{t["url"]}" class="em-cta" style="margin-bottom:0.5rem;display:block">'
        f'{t["nombre"]} — empezar a grabar ↗</a>'
        for t in tokens
    )

    html = (
        html
        .replace("Hola, {{COMPRADOR_NOMBRE}}", "Hola")
        .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
        .replace("{{INTEGRANTE_NOMBRE}}", "")
        .replace("{{FECHA_HORA}}", "")
        .replace('<a href="{{DASHBOARD_URL}}" class="em-cta">Ver progreso de la familia ↗</a>', tokens_html)
    )

    try:
        resend.api_key = key
        resend.Emails.send({
            "from": _FROM,
            "to": [email_comprador],
            "subject": f"Ya podés empezar — {nombre_familia}",
            "html": html,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando bienvenida a %s: %s", email_comprador, exc)


def send_libro_listo(email_comprador: str, nombre_familia: str, signed_url: str) -> None:
    key = _get_key()
    if not key:
        return

    html = (_TEMPLATES_DIR / "email_entrega.html").read_text()

    hoy = datetime.now().strftime("%d/%m/%Y")

    html = (
        html
        .replace("{{FAMILIA_NOMBRE}}", nombre_familia)
        .replace("Hola, {{COMPRADOR_NOMBRE}}.", "Hola.")
        .replace("{{PDF_URL}}", signed_url)
        .replace("{{DASHBOARD_URL}}", "#")
        .replace("{{AÑO}}", str(datetime.now().year))
        .replace("{{TOTAL_HISTORIAS}}", "")
        .replace("{{FECHA_GENERACION}}", hoy)
    )

    try:
        resend.api_key = key
        resend.Emails.send({
            "from": _FROM,
            "to": [email_comprador],
            "subject": f"Tu libro está listo — {nombre_familia}",
            "html": html,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando libro_listo a %s: %s", email_comprador, exc)


def send_recordatorio(
    email_integrante: str,
    nombre_integrante: str,
    nombre_familia: str,
    token_url: str,
) -> None:
    key = _get_key()
    if not key:
        return

    html = (_TEMPLATES_DIR / "email_reminder.html").read_text()

    html = (
        html
        .replace("{{NOMBRE}}", nombre_integrante)
        .replace("{{RECORDING_URL}}", token_url)
    )

    try:
        resend.api_key = key
        resend.Emails.send({
            "from": _FROM,
            "to": [email_integrante],
            "subject": f"{nombre_familia} te está esperando",
            "html": html,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando recordatorio a %s: %s", email_integrante, exc)
