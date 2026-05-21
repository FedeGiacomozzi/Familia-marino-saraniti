"""
Layout agent: genera el PDF del libro en formato A5 usando WeasyPrint.
Estructura: Tapa → Blanco → Prólogo → [Capítulo + Transición] × N → Epílogo → Timeline
"""

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from weasyprint import HTML, CSS

from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

# ─── Paleta y tipografía ──────────────────────────────────────────────────────
COLOR_FONDO = "#FAF8F5"
COLOR_TEXTO = "#2C2C2C"
COLOR_ACENTO = "#8B6F5E"

CSS_BASE = f"""
@import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Montserrat:wght@300;400;600&display=swap');

@page {{
  size: A5;
  margin: 22mm 18mm 24mm 22mm;
  @bottom-center {{
    content: counter(page);
    font-family: 'Montserrat', sans-serif;
    font-size: 9pt;
    color: {COLOR_ACENTO};
  }}
}}

@page :first {{ @bottom-center {{ content: none; }} }}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
  font-family: 'Lora', serif;
  font-size: 10.5pt;
  line-height: 1.7;
  color: {COLOR_TEXTO};
  background: {COLOR_FONDO};
}}

h1 {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 300;
  font-size: 28pt;
  letter-spacing: 0.05em;
  color: {COLOR_TEXTO};
  margin-bottom: 0.3em;
}}

h2 {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 600;
  font-size: 11pt;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_ACENTO};
  margin-bottom: 1.5em;
}}

h3 {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 300;
  font-size: 18pt;
  color: {COLOR_TEXTO};
  margin-bottom: 0.5em;
}}

p {{
  margin-bottom: 0.9em;
  text-align: justify;
  hyphens: auto;
}}

em {{ font-style: italic; }}

.page-break {{ page-break-after: always; }}
.page-break-before {{ page-break-before: always; }}

/* Tapa */
.cover {{
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 100%;
  min-height: 180mm;
  padding-bottom: 20mm;
}}

.cover-linea {{
  width: 40mm;
  height: 1px;
  background: {COLOR_ACENTO};
  margin-bottom: 8mm;
}}

.cover-titulo {{
  font-family: 'Lora', serif;
  font-size: 26pt;
  font-weight: 400;
  line-height: 1.2;
  color: {COLOR_TEXTO};
  margin-bottom: 4mm;
}}

.cover-subtitulo {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 300;
  font-size: 9pt;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: {COLOR_ACENTO};
}}

/* Foto en capítulo */
.foto-capitulo {{
  width: 100%;
  max-height: 60mm;
  object-fit: cover;
  margin-bottom: 6mm;
  display: block;
}}

/* Transición */
.transicion {{
  margin: 6mm 0;
  padding: 4mm 6mm;
  border-left: 2px solid {COLOR_ACENTO};
  font-style: italic;
  color: {COLOR_ACENTO};
  font-size: 9.5pt;
}}

/* Prólogo / Epílogo */
.seccion-header {{
  margin-bottom: 8mm;
}}

/* Timeline */
.timeline-item {{
  display: flex;
  gap: 6mm;
  margin-bottom: 5mm;
  font-size: 9pt;
}}

.timeline-fecha {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 600;
  color: {COLOR_ACENTO};
  min-width: 20mm;
  font-size: 8.5pt;
}}

.timeline-nombre {{
  color: {COLOR_TEXTO};
}}
"""


def _texto_a_html(texto: str) -> str:
    """Convert plain text to HTML paragraphs, rendering *italic* and **bold** markers."""
    paragraphs = [p.strip() for p in texto.split("\n\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        # Convert markdown bold/italic to HTML (order matters: ** before *)
        p = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p)
        p = re.sub(r'\*(.+?)\*', r'<em>\1</em>', p)
        if p.startswith("—"):
            html_parts.append(f'<p class="cita">{p}</p>')
        else:
            html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def _foto_tag(foto_path: str | None) -> str:
    if foto_path and os.path.exists(foto_path):
        return f'<img class="foto-capitulo" src="file://{foto_path}" alt="foto"/>'
    return ""


def _build_html(
    manuscript: BookManuscript,
    nombre_familia: str,
    fotos: dict[str, str],
    personas_meta: list[dict],
) -> str:
    partes = []

    # ── Tapa ──
    partes.append(f"""
<div class="cover page-break">
  <div class="cover-linea"></div>
  <div class="cover-titulo">{nombre_familia}</div>
  <div class="cover-subtitulo">Memorias · {datetime.now().year}</div>
</div>
""")

    # ── Página blanca ──
    partes.append('<div class="page-break"></div>')

    # ── Prólogo ──
    partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header">
    <h2>Prólogo</h2>
  </div>
  {_texto_a_html(manuscript.prologo)}
</div>
""")

    # ── Capítulos + Transiciones ──
    for i, nombre in enumerate(manuscript.orden):
        capitulo = manuscript.capitulos.get(nombre, "")
        if not capitulo:
            continue

        foto_tag = _foto_tag(fotos.get(nombre))

        partes.append(f"""
<div class="page-break-before">
  {foto_tag}
  <div class="seccion-header">
    <h3>{nombre}</h3>
  </div>
  {_texto_a_html(capitulo)}
</div>
""")

        # Transición hacia el siguiente
        if i < len(manuscript.orden) - 1:
            siguiente = manuscript.orden[i + 1]
            key = f"{nombre}→{siguiente}"
            trans = manuscript.transiciones.get(key, "")
            if trans:
                partes.append(f'<div class="transicion">{_texto_a_html(trans)}</div>')

    # ── Epílogo ──
    partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header">
    <h2>Epílogo</h2>
  </div>
  {_texto_a_html(manuscript.epilogo)}
</div>
""")

    # ── Timeline / árbol ──
    integrantes = personas_meta  # may include rol, fecha_fallec from familia sheet
    personas_con_fecha = sorted(
        [p for p in integrantes if p.get("fecha_nac")],
        key=lambda p: _sort_fecha(p.get("fecha_nac", "")),
    )

    if personas_con_fecha:
        timeline_items = []
        for p in personas_con_fecha:
            nombre_p = p["nombre"]
            fecha_nac = p.get("fecha_nac", "")
            fecha_fallec = p.get("fecha_fallec", "")
            rol = p.get("rol", "")
            vive = p.get("vive", True)

            fechas = fecha_nac
            if fecha_fallec:
                fechas += f" – {fecha_fallec}"
            elif not vive:
                fechas += " – †"

            rol_tag = f'<span style="color:{COLOR_ACENTO};font-size:8pt;margin-left:4mm">{rol}</span>' if rol else ""
            en_libro = "●" if nombre_p in manuscript.orden else "○"

            timeline_items.append(f"""<div class="timeline-item">
  <div class="timeline-fecha">{fechas}</div>
  <div class="timeline-nombre">{en_libro} {nombre_p} {rol_tag}</div>
</div>""")

        partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header">
    <h2>Cronología</h2>
  </div>
  <p style="font-size:8.5pt;color:{COLOR_ACENTO};margin-bottom:5mm">
    ● incluido en el libro &nbsp;&nbsp; ○ integrante de la familia
  </p>
  {"".join(timeline_items)}
</div>
""")

    body = "\n".join(partes)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"/>
<style>
{CSS_BASE}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _sort_fecha(fecha: str) -> str:
    """Convert dd-mm-aaaa or dd/mm/aaaa to aaaa-mm-dd for sorting."""
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", fecha)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return fecha


def run(
    manuscript: BookManuscript,
    personas_meta: list[dict],
    nombre_familia: str = "Familia Mariño · Saraniti",
    output_path: str | None = None,
    todos_integrantes: list[dict] | None = None,
) -> str:
    """
    Genera el PDF y devuelve su path.
    personas_meta: list of {nombre, fecha_nac, rol, fecha_fallec, vive} — los que tienen capítulo
    todos_integrantes: full familia list for the timeline (includes people without chapters)
    """
    # Descargar fotos a /tmp/
    fotos: dict[str, str] = {}
    for p in personas_meta:
        nombre = p["nombre"]
        try:
            foto_url = sheets.get_foto_url(nombre)
            if foto_url:
                ext = ".jpg"
                dest = f"/tmp/foto_{re.sub(r'[^a-zA-Z0-9]', '_', nombre)}{ext}"
                sheets.download_drive_file(foto_url, dest)
                fotos[nombre] = dest
        except Exception as e:
            print(f"[layout] No se pudo descargar foto de {nombre}: {e}")

    # Use full integrantes list for timeline if available, else fall back to personas_meta
    timeline_personas = todos_integrantes if todos_integrantes else personas_meta
    html_content = _build_html(manuscript, nombre_familia, fotos, timeline_personas)

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"/tmp/libro_{ts}.pdf"

    HTML(string=html_content).write_pdf(
        output_path,
        stylesheets=[CSS(string=CSS_BASE)],
    )

    return output_path
