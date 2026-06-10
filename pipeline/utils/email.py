import logging
import os
import re
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_FROM = "hola@ethosbios.com"
_POSTMARK_URL = "https://api.postmarkapp.com/email"


def _get_key() -> str | None:
    key = os.environ.get("POSTMARK_API_KEY", "")
    if not key:
        logger.warning("POSTMARK_API_KEY no configurada")
        return None
    return key


def _html_to_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r" {2,}", " ", text).strip()


def _send(key: str, to: str, subject: str, html: str) -> None:
    resp = httpx.post(
        _POSTMARK_URL,
        headers={"X-Postmark-Server-Token": key, "Content-Type": "application/json"},
        json={
            "From": _FROM,
            "To": to,
            "Subject": subject,
            "HtmlBody": html,
            "TextBody": _html_to_text(html),
        },
        timeout=15,
    )
    if not resp.is_success:
        logger.error(
            "Postmark error %s — %s — body: %s",
            resp.status_code,
            subject,
            resp.text,
        )
    resp.raise_for_status()


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
        _send(key, email_comprador, f"Ya podés empezar — {nombre_familia}", html)
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
        _send(key, email_comprador, f"Tu libro está listo — {nombre_familia}", html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando libro_listo a %s: %s", email_comprador, exc)


def send_magic_link(email: str, nombre_familia: str, magic_link_url: str) -> None:
    key = _get_key()
    if not key:
        return

    html = (_TEMPLATES_DIR / "email_magic_link.html").read_text()
    html = (
        html
        .replace("{{NOMBRE_FAMILIA}}", nombre_familia)
        .replace("{{MAGIC_LINK}}", magic_link_url)
        .replace("{{AÑO}}", str(datetime.now().year))
    )

    try:
        _send(key, email, f"Tu link de acceso — {nombre_familia}", html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando magic link a %s: %s", email, exc)


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
        _send(key, email_integrante, f"{nombre_familia} te está esperando", html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error enviando recordatorio a %s: %s", email_integrante, exc)
