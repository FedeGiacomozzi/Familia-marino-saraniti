"""
layout_agent.py — Convierte BookManuscript a PDF A5.

Recibe el BookManuscript del editor_agent y produce:
  1. HTML con estructura de libro biográfico
  2. PDF A5 (148mm × 210mm) vía WeasyPrint

Tipografía : Lora (cuerpo) + Montserrat (títulos) vía Google Fonts
Paleta     : crema cálido (#FAF8F5), texto oscuro (#2C2C2C), acento tierra (#8B6F5E)

Árbol genealógico v1: línea de tiempo por fecha de nacimiento.
Relaciones familiares completas → v2 (requiere datos adicionales en el pipeline).
"""

import logging
import os
import re
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

A5_WIDTH_MM  = 148
A5_HEIGHT_MM = 210


# ── CSS del libro ──────────────────────────────────────────────────────────────

BOOK_CSS = textwrap.dedent("""\
    @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Montserrat:wght@300;600&display=swap');

    @page {
        size: 148mm 210mm;
        margin: 22mm 18mm 28mm 22mm;

        @bottom-center {
            content: counter(page);
            font-family: 'Montserrat', sans-serif;
            font-size: 8pt;
            color: #999;
        }
    }

    @page cover    { margin: 0; @bottom-center { content: none; } }
    @page no-num   { @bottom-center { content: none; } }
    @page chapter-start { margin-top: 35mm; }

    * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
    }

    body {
        font-family: 'Lora', Georgia, serif;
        font-size: 10.5pt;
        line-height: 1.75;
        color: #2C2C2C;
        background: #FAF8F5;
        text-align: justify;
        hyphens: auto;
    }

    /* ── Portada ── */
    .cover {
        page: cover;
        width: 148mm;
        height: 210mm;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        background: #2C2C2C;
        color: #FAF8F5;
        text-align: center;
        padding: 20mm;
        page-break-after: always;
    }

    .cover-eyebrow {
        font-family: 'Montserrat', sans-serif;
        font-weight: 300;
        font-size: 8pt;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #8B6F5E;
        margin-bottom: 12mm;
    }

    .cover-title {
        font-family: 'Lora', Georgia, serif;
        font-size: 26pt;
        font-weight: 600;
        line-height: 1.2;
        margin-bottom: 8mm;
    }

    .cover-subtitle {
        font-family: 'Montserrat', sans-serif;
        font-weight: 300;
        font-size: 9pt;
        letter-spacing: 1px;
        color: #aaa;
    }

    .cover-year {
        position: absolute;
        bottom: 14mm;
        font-family: 'Montserrat', sans-serif;
        font-size: 8pt;
        color: #666;
    }

    /* ── Página de cortesía ── */
    .blank-page {
        page: no-num;
        height: 210mm;
        page-break-after: always;
    }

    /* ── Prólogo y epílogo ── */
    .section {
        page: no-num;
        page-break-before: always;
        page-break-after: always;
    }

    .section-title {
        font-family: 'Montserrat', sans-serif;
        font-size: 8pt;
        font-weight: 600;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #8B6F5E;
        margin-bottom: 10mm;
        text-align: center;
    }

    /* ── Capítulo ── */
    .chapter {
        page: chapter-start;
        page-break-before: always;
        page-break-after: always;
    }

    .chapter-number {
        font-family: 'Montserrat', sans-serif;
        font-size: 7pt;
        font-weight: 300;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #8B6F5E;
        text-align: center;
        margin-bottom: 4mm;
    }

    .chapter-name {
        font-family: 'Lora', Georgia, serif;
        font-size: 18pt;
        font-weight: 600;
        text-align: center;
        margin-bottom: 2mm;
        color: #2C2C2C;
    }

    .chapter-date {
        font-family: 'Montserrat', sans-serif;
        font-size: 7.5pt;
        font-weight: 300;
        color: #999;
        text-align: center;
        margin-bottom: 12mm;
    }

    .chapter-rule {
        width: 20mm;
        height: 1px;
        background: #8B6F5E;
        margin: 0 auto 12mm auto;
    }

    /* ── Transición ── */
    .transition {
        font-style: italic;
        color: #666;
        text-align: center;
        margin: 10mm 8mm;
        line-height: 1.9;
        font-size: 9.5pt;
        page-break-inside: avoid;
    }

    /* ── Cuerpo de texto ── */
    p {
        margin-bottom: 0;
        text-indent: 5mm;
    }

    p:first-of-type {
        text-indent: 0;
    }

    p + p {
        margin-top: 0;
    }

    /* ── Línea de tiempo ── */
    .timeline {
        page: no-num;
        page-break-before: always;
    }

    .timeline-title {
        font-family: 'Montserrat', sans-serif;
        font-size: 8pt;
        font-weight: 600;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #8B6F5E;
        text-align: center;
        margin-bottom: 12mm;
    }

    .timeline-item {
        display: flex;
        gap: 6mm;
        margin-bottom: 6mm;
        align-items: baseline;
    }

    .timeline-year {
        font-family: 'Montserrat', sans-serif;
        font-size: 8pt;
        font-weight: 600;
        color: #8B6F5E;
        min-width: 18mm;
        flex-shrink: 0;
    }

    .timeline-name {
        font-family: 'Lora', Georgia, serif;
        font-size: 10pt;
    }

    .timeline-dot {
        width: 2mm;
        height: 2mm;
        border-radius: 50%;
        background: #8B6F5E;
        flex-shrink: 0;
        margin-top: 2mm;
    }
""")


# ── Generación de HTML ─────────────────────────────────────────────────────────

def _build_html(manuscript, familia: str, anio: str) -> str:
    """Construye el HTML completo del libro a partir del BookManuscript."""

    partes = []

    # Portada
    partes.append(_render_cover(familia, anio))

    # Página de cortesía
    partes.append('<div class="blank-page"></div>')

    # Prólogo
    partes.append(_render_section("Prólogo", manuscript.prologo))

    # Capítulos + transiciones
    for i, nombre in enumerate(manuscript.orden):
        capitulo = manuscript.capitulos.get(nombre, "")
        if not capitulo:
            continue

        # Fecha de nacimiento para el subtítulo
        fecha = ""
        # Buscar en vp_map si está disponible — si no, vacío
        for vp in getattr(manuscript, "_voice_profiles", []):
            if vp.nombre == nombre:
                fecha = vp.fecha_nac
                break

        partes.append(_render_chapter(i + 1, nombre, fecha, capitulo))

        # Transición al siguiente (si existe)
        if i < len(manuscript.orden) - 1:
            nombre_siguiente = manuscript.orden[i + 1]
            clave = f"{nombre}->{nombre_siguiente}"
            transicion = manuscript.transiciones.get(clave, "")
            if transicion:
                partes.append(f'<div class="transition">{_nl2p(transicion)}</div>')

    # Epílogo
    partes.append(_render_section("Epílogo", manuscript.epilogo))

    # Línea de tiempo
    partes.append(_render_timeline(manuscript))

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
{BOOK_CSS}
</style>
</head>
<body>
{"".join(partes)}
</body>
</html>"""

    return html


def _render_cover(familia: str, anio: str) -> str:
    return f"""
<div class="cover">
    <div class="cover-eyebrow">Libro Familiar</div>
    <div class="cover-title">{familia}</div>
    <div class="cover-subtitle">Historia en voces propias</div>
    <div class="cover-year">{anio}</div>
</div>"""


def _render_section(titulo: str, texto: str) -> str:
    return f"""
<div class="section">
    <div class="section-title">{titulo}</div>
    {_text_to_paragraphs(texto)}
</div>"""


def _render_chapter(numero: int, nombre: str, fecha: str, texto: str) -> str:
    fecha_html = f'<div class="chapter-date">{fecha}</div>' if fecha else ""
    return f"""
<div class="chapter">
    <div class="chapter-number">Capítulo {_roman(numero)}</div>
    <div class="chapter-name">{nombre}</div>
    {fecha_html}
    <div class="chapter-rule"></div>
    {_text_to_paragraphs(texto)}
</div>"""


def _render_timeline(manuscript) -> str:
    """Línea de tiempo simple por fecha de nacimiento."""
    personas = []
    for nombre in manuscript.orden:
        fecha = ""
        for vp in getattr(manuscript, "_voice_profiles", []):
            if vp.nombre == nombre:
                fecha = vp.fecha_nac
                break
        personas.append((nombre, fecha))

    # Ordenar por fecha (ya deberían estar ordenadas, pero por las dudas)
    personas.sort(key=lambda x: x[1] or "9999")

    items = ""
    for nombre, fecha in personas:
        anio = fecha[:4] if fecha and len(fecha) >= 4 else "—"
        items += f"""
        <div class="timeline-item">
            <div class="timeline-year">{anio}</div>
            <div class="timeline-dot"></div>
            <div class="timeline-name">{nombre}</div>
        </div>"""

    return f"""
<div class="timeline">
    <div class="timeline-title">Las personas de este libro</div>
    {items}
</div>"""


def _text_to_paragraphs(texto: str) -> str:
    """Convierte texto plano con saltos de línea en párrafos HTML."""
    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{_escape(p)}</p>" for p in parrafos)


def _nl2p(texto: str) -> str:
    """Convierte saltos simples en <br> para transiciones cortas."""
    return _escape(texto).replace("\n", "<br>")


def _escape(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _roman(n: int) -> str:
    vals = [(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    result = ""
    for v, s in vals:
        while n >= v:
            result += s
            n -= v
    return result


# ── PDF via WeasyPrint ─────────────────────────────────────────────────────────

def _html_to_pdf(html: str, output_path: str) -> None:
    try:
        from weasyprint import HTML, CSS
        HTML(string=html).write_pdf(output_path)
        logger.info("PDF generado: %s", output_path)
    except ImportError:
        raise RuntimeError(
            "WeasyPrint no está instalado. "
            "Agregalo a requirements.txt: weasyprint>=61.0"
        )


# ── Función principal ──────────────────────────────────────────────────────────

def run(
    manuscript,
    familia: str,
    output_path: str | None = None,
    upload_to_drive: bool = False,
) -> str:
    """
    Genera el PDF A5 del libro.

    manuscript:     BookManuscript del editor_agent
    familia:        nombre de la familia para la portada (ej: "Mariño-Saraniti")
    output_path:    ruta de salida. Default: /tmp/libro_{familia}.pdf
    upload_to_drive: si True, sube el PDF a Google Drive y devuelve el link

    Devuelve la ruta local del PDF generado.
    """
    import re
    from datetime import datetime

    if not output_path:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", familia)
        output_path = f"/tmp/libro_{slug}.pdf"

    anio = datetime.now().strftime("%Y")

    logger.info("Generando HTML del libro '%s'...", familia)
    html = _build_html(manuscript, familia, anio)

    # Guardar HTML para debug si se pide
    html_path = output_path.replace(".pdf", ".html")
    Path(html_path).write_text(html, encoding="utf-8")
    logger.info("HTML guardado en: %s", html_path)

    logger.info("Convirtiendo a PDF A5...")
    _html_to_pdf(html, output_path)

    size_kb = Path(output_path).stat().st_size // 1024
    logger.info("PDF listo: %s (%d KB)", output_path, size_kb)

    if upload_to_drive:
        drive_url = _upload_to_drive(output_path, familia)
        logger.info("PDF subido a Drive: %s", drive_url)
        return drive_url

    return output_path


def _upload_to_drive(pdf_path: str, familia: str) -> str:
    """Sube el PDF a la carpeta de Drive del proyecto."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    from utils.secrets import get_google_credentials

    FOLDER_ID = "1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3"

    creds = get_google_credentials()
    service = build("drive", "v3", credentials=creds)

    nombre_archivo = f"Libro_{familia.replace(' ', '_')}.pdf"
    metadata = {"name": nombre_archivo, "parents": [FOLDER_ID]}
    media = MediaFileUpload(pdf_path, mimetype="application/pdf")

    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    try:
        service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone"},
        ).execute()
    except Exception:
        pass

    return file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    print("layout_agent requiere un BookManuscript del editor_agent.")
    print("Uso desde pipeline: POST /run/pipeline o /run/layout")
