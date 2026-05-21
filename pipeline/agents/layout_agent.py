"""
Layout agent: genera el PDF del libro en formato A5 usando WeasyPrint.
Estructura: Tapa → Blanco → Prólogo → [Árbol + Capítulo] × N → Epílogo → Cronología
"""

import os
import re
import unicodedata
from datetime import datetime

from weasyprint import HTML, CSS

from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import sheets

COLOR_FONDO = "#FAF8F5"
COLOR_TEXTO = "#2C2C2C"
COLOR_ACENTO = "#8B6F5E"
COLOR_RAMA = "#7B5E45"
COLOR_HOJA = "#A8C99A"
COLOR_HOJA2 = "#B8D4A8"
COLOR_HOJA3 = "#C5DDB8"

CSS_BASE = f"""
@import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Montserrat:wght@300;400;600&display=swap');

@page {{
  size: A5;
  margin: 22mm 18mm 24mm 22mm;
  background: {COLOR_FONDO};
  @bottom-center {{
    content: counter(page);
    font-family: 'Montserrat', sans-serif;
    font-size: 9pt;
    color: {COLOR_ACENTO};
  }}
}}

@page :first {{ @bottom-center {{ content: none; }} }}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

html, body {{
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

p.apertura {{
  font-style: italic;
  font-size: 11.5pt;
  color: {COLOR_ACENTO};
  margin-bottom: 1.4em;
  margin-top: 0.5em;
}}

em {{ font-style: italic; }}

.page-break {{ page-break-after: always; }}
.page-break-before {{ page-break-before: always; }}

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

.foto-capitulo {{
  width: 100%;
  height: auto;
  max-width: 100%;
  margin-bottom: 5mm;
  display: block;
}}

.foto-placeholder {{
  width: 100%;
  height: 55mm;
  background: #EDE8E0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 5mm;
  font-family: 'Lora', serif;
  font-style: italic;
  font-size: 32pt;
  color: {COLOR_ACENTO};
}}

.transicion {{
  margin: 6mm 0;
  padding: 4mm 6mm;
  border-left: 2px solid {COLOR_ACENTO};
  font-style: italic;
  color: {COLOR_ACENTO};
  font-size: 9.5pt;
}}

.seccion-header {{
  margin-bottom: 8mm;
}}

.cap-numero {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 300;
  font-size: 9pt;
  letter-spacing: 0.25em;
  text-transform: uppercase;
  color: {COLOR_ACENTO};
  margin-bottom: 3mm;
}}

.arbol-pagina {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 180mm;
}}

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


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().lower()


def _texto_a_html(texto: str) -> str:
    """Convert plain text to HTML paragraphs, rendering *italic* and **bold** markers."""
    paragraphs = [p.strip() for p in texto.split("\n\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        p = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p)
        p = re.sub(r'\*(.+?)\*', r'<em>\1</em>', p)
        if p.startswith("—"):
            html_parts.append(f'<p class="cita">{p}</p>')
        else:
            html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def _texto_a_html_con_apertura(texto: str) -> str:
    """Like _texto_a_html but styles the first paragraph as apertura (hook)."""
    paragraphs = [p.strip() for p in texto.split("\n\n") if p.strip()]
    html_parts = []
    for i, p in enumerate(paragraphs):
        p = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p)
        p = re.sub(r'\*(.+?)\*', r'<em>\1</em>', p)
        if i == 0:
            html_parts.append(f'<p class="apertura">{p}</p>')
        elif p.startswith("—"):
            html_parts.append(f'<p class="cita">{p}</p>')
        else:
            html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def _inicial(nombre: str) -> str:
    return nombre.strip()[0].upper() if nombre.strip() else "?"


# ─── SVG del árbol creciente ─────────────────────────────────────────────────

def _build_family_graph(personas: list[str], relaciones: list[dict]):
    """Returns couples list and parents_of dict from relaciones, filtered to personas in book.
    Uses first-name fallback so short names in the sheet match full names in the book."""
    norm_map = {_norm(p): p for p in personas}
    first_name_map: dict[str, str] = {}
    for norm_name, canonical in norm_map.items():
        first = norm_name.split()[0]
        if first not in first_name_map:
            first_name_map[first] = canonical

    def _resolve(name: str) -> str | None:
        n = _norm(name)
        if n in norm_map:
            return norm_map[n]
        first = n.split()[0] if n else ""
        return first_name_map.get(first)

    couples = []
    parents_of = {p: [] for p in personas}

    for r in relaciones:
        a = _resolve(r["persona_a"])
        b = _resolve(r["persona_b"])
        if a is None or b is None:
            continue
        rel = r["relacion"]
        if rel in ("cónyuge", "conyuge"):
            if (a, b) not in couples and (b, a) not in couples:
                couples.append((a, b))
        elif rel in ("padre", "madre"):
            if a not in parents_of[b]:
                parents_of[b].append(a)
    return couples, parents_of


def _assign_generations(personas: list[str], parents_of: dict) -> dict:
    gens = {}
    for p in personas:
        if not parents_of.get(p):
            gens[p] = 0
    changed = True
    while changed:
        changed = False
        for p in personas:
            if p in gens:
                continue
            pars = parents_of.get(p, [])
            if pars and all(pp in gens for pp in pars):
                gens[p] = max(gens[pp] for pp in pars) + 1
                changed = True
    for p in personas:
        if p not in gens:
            gens[p] = 0
    return gens


def _build_tree_svg(
    all_personas: list[str],
    visible_up_to_idx: int,
    current_nombre: str,
    relaciones: list[dict],
    width: int = 240,
    height: int = 280,
) -> str:
    visible = all_personas[: visible_up_to_idx + 1]
    couples, parents_of = _build_family_graph(visible, relaciones)
    gens = _assign_generations(visible, parents_of)

    max_gen = max(gens.values()) if gens else 0
    gen_groups: dict[int, list[str]] = {}
    for p, g in gens.items():
        gen_groups.setdefault(g, []).append(p)
    for g in gen_groups:
        gen_groups[g].sort(key=lambda p: all_personas.index(p) if p in all_personas else 0)

    # Layout: gen 0 (oldest) near bottom, gen max (youngest) near top — árbol crece hacia arriba
    pad_x = 28
    node_bottom = int(height * 0.62)   # gen 0 y position
    node_top = int(height * 0.22)       # gen max y position
    usable_h = node_bottom - node_top

    gen_y = {}
    for g in range(max_gen + 1):
        ratio = g / max(max_gen, 1) if max_gen > 0 else 0
        gen_y[g] = node_bottom - int(ratio * usable_h)

    node_pos: dict[str, tuple[float, float]] = {}
    for g, members in gen_groups.items():
        n = len(members)
        for i, p in enumerate(members):
            x = pad_x + (i + 0.5) * (width - 2 * pad_x) / n
            node_pos[p] = (x, gen_y[g])

    elements = []

    # Trunk: desde abajo hasta justo debajo de la generación más vieja
    trunk_x = width // 2
    oldest_y = max(y for _, y in node_pos.values()) if node_pos else node_bottom
    trunk_top = oldest_y + 36
    trunk_bot = height + 10
    elements.append(
        f'<path d="M{trunk_x} {trunk_bot} Q{trunk_x} {trunk_top + 20} {trunk_x} {trunk_top}" '
        f'stroke="{COLOR_RAMA}" stroke-width="3" fill="none" stroke-linecap="round"/>'
    )

    # Ramas: desde el tronco hacia cada nodo de gen 0, y desde midpoint de padres hacia hijos
    for p, (x, y) in node_pos.items():
        opacity = "1" if p == current_nombre else "0.55"
        pars = parents_of.get(p, [])
        par_positions = [node_pos[par] for par in pars if par in node_pos]
        if par_positions:
            # Nace del punto medio entre los padres
            origin_x = sum(pos[0] for pos in par_positions) / len(par_positions)
            origin_y = sum(pos[1] for pos in par_positions) / len(par_positions)
        else:
            # Nace del tronco
            origin_x = trunk_x
            origin_y = trunk_top
        ctrl_x = (origin_x + x) / 2
        ctrl_y = (origin_y + y) / 2
        elements.append(
            f'<path d="M{origin_x:.1f} {origin_y:.1f} Q{ctrl_x:.1f} {ctrl_y:.1f} {x:.1f} {y + 32:.1f}" '
            f'stroke="{COLOR_RAMA}" stroke-width="2" fill="none" stroke-linecap="round" opacity="{opacity}"/>'
        )

    # Couple lines (dashed)
    for (a, b) in couples:
        if a in node_pos and b in node_pos:
            ax, ay = node_pos[a]
            bx, by = node_pos[b]
            mx = (ax + bx) / 2
            my = (ay + by) / 2
            elements.append(
                f'<path d="M{ax} {ay} Q{mx} {my - 8} {bx} {by}" '
                f'stroke="{COLOR_ACENTO}" stroke-width="1" fill="none" stroke-dasharray="3,3" opacity="0.7"/>'
            )

    # Scatter some leaves
    import random
    rng = random.Random(42)
    for p, (x, y) in node_pos.items():
        for _ in range(3):
            lx = x + rng.uniform(-28, 28)
            ly = y - rng.uniform(10, 40)
            rx, ry = rng.uniform(6, 10), rng.uniform(3, 6)
            angle = rng.uniform(-40, 40)
            col = rng.choice([COLOR_HOJA, COLOR_HOJA2, COLOR_HOJA3])
            op = 0.75 if p == current_nombre else 0.45
            elements.append(
                f'<ellipse cx="{lx:.1f}" cy="{ly:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
                f'fill="{col}" opacity="{op}" transform="rotate({angle:.0f} {lx:.1f} {ly:.1f})"/>'
            )

    # Nodes
    for p, (x, y) in node_pos.items():
        is_current = p == current_nombre
        r_outer = 34 if is_current else 28
        r_inner = 30 if is_current else 24
        stroke_w = "3" if is_current else "1.5"
        stroke_col = COLOR_RAMA if is_current else "#B5A090"
        fill_inner = "#F0EBE3" if is_current else COLOR_FONDO
        text_col = COLOR_RAMA if is_current else "#B5A090"
        font_size = 26 if is_current else 20
        name_col = "#5C4A3A" if is_current else "#9A8878"
        first_name = p.split()[0]

        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_outer}" '
            f'fill="{COLOR_FONDO}" stroke="{stroke_col}" stroke-width="{stroke_w}"/>'
        )
        elements.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_inner}" fill="{fill_inner}" stroke="none"/>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{y + 8:.1f}" text-anchor="middle" '
            f'font-family="Georgia, serif" font-style="italic" font-size="{font_size}" '
            f'fill="{text_col}">{_inicial(p)}</text>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{y + r_outer + 14:.1f}" text-anchor="middle" '
            f'font-family="Georgia, serif" font-size="8.5" fill="{name_col}" '
            f'letter-spacing="0.05em">{first_name}</text>'
        )

    svg = (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">\n'
        + "\n".join(elements)
        + "\n</svg>"
    )
    return svg


# ─── Foto / placeholder ───────────────────────────────────────────────────────

def _foto_tag(foto_path: str | None, nombre: str) -> str:
    if foto_path and os.path.exists(foto_path):
        return f'<img class="foto-capitulo" src="file://{foto_path}" alt="foto de {nombre}"/>'
    inicial = _inicial(nombre)
    return f'<div class="foto-placeholder">{inicial}</div>'


# ─── HTML principal ───────────────────────────────────────────────────────────

def _build_html(
    manuscript: BookManuscript,
    nombre_familia: str,
    fotos: dict[str, str],
    personas_meta: list[dict],
    relaciones: list[dict],
) -> str:
    partes = []

    # ── Tapa
    partes.append(f"""
<div class="cover page-break">
  <div class="cover-linea"></div>
  <div class="cover-titulo">{nombre_familia}</div>
  <div class="cover-subtitulo">Memorias · {datetime.now().year}</div>
</div>
""")

    partes.append('<div class="page-break"></div>')

    # ── Prólogo
    partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header"><h2>Prólogo</h2></div>
  {_texto_a_html(manuscript.prologo)}
</div>
""")

    # ── Capítulos
    orden = manuscript.orden
    for i, nombre in enumerate(orden):
        capitulo = manuscript.capitulos.get(nombre, "")
        if not capitulo:
            continue

        cap_num = i + 1

        # Página 1: árbol creciente
        svg = _build_tree_svg(orden, i, nombre, relaciones)
        first_name = nombre.split()[0]
        partes.append(f"""
<div class="arbol-pagina page-break-before page-break">
  <div class="cap-numero">Capítulo {cap_num}</div>
  <h3 style="margin-bottom: 8mm; text-align:center">{first_name}</h3>
  {svg}
</div>
""")

        # Página 2: foto + texto
        foto = _foto_tag(fotos.get(nombre), nombre)
        partes.append(f"""
<div class="page-break-before">
  {foto}
  {_texto_a_html_con_apertura(capitulo)}
</div>
""")

        # Transición
        if i < len(orden) - 1:
            siguiente = orden[i + 1]
            key = f"{nombre}→{siguiente}"
            trans = manuscript.transiciones.get(key, "")
            if trans:
                partes.append(f'<div class="transicion">{_texto_a_html(trans)}</div>')

    # ── Epílogo
    partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header"><h2>Epílogo</h2></div>
  {_texto_a_html(manuscript.epilogo)}
</div>
""")

    # ── Cronología
    personas_con_fecha = sorted(
        [p for p in personas_meta if p.get("fecha_nac")],
        key=lambda p: _sort_fecha(p.get("fecha_nac", "")),
    )

    if personas_con_fecha:
        items = []
        for p in personas_con_fecha:
            nombre_p = p["nombre"]
            fechas = p.get("fecha_nac", "")
            fd = p.get("fecha_fallec", "")
            if fd:
                fechas += f" – {fd}"
            elif not p.get("vive", True):
                fechas += " – †"
            rol = p.get("rol", "")
            rol_tag = f'<span style="color:{COLOR_ACENTO};font-size:8pt;margin-left:4mm">{rol}</span>' if rol else ""
            en_libro = "●" if nombre_p in manuscript.orden else "○"
            items.append(f"""<div class="timeline-item">
  <div class="timeline-fecha">{fechas}</div>
  <div class="timeline-nombre">{en_libro} {nombre_p} {rol_tag}</div>
</div>""")

        partes.append(f"""
<div class="page-break-before">
  <div class="seccion-header"><h2>Cronología</h2></div>
  <p style="font-size:8.5pt;color:{COLOR_ACENTO};margin-bottom:5mm">
    ● incluido en el libro &nbsp;&nbsp; ○ integrante de la familia
  </p>
  {"".join(items)}
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
    relaciones: list[dict] | None = None,
) -> str:
    fotos: dict[str, str] = {}
    for p in personas_meta:
        nombre = p["nombre"]
        try:
            foto_url = sheets.get_foto_url(nombre)
            if foto_url:
                dest = f"/tmp/foto_{re.sub(r'[^a-zA-Z0-9]', '_', nombre)}.jpg"
                sheets.download_drive_file(foto_url, dest)
                fotos[nombre] = dest
        except Exception as e:
            print(f"[layout] No se pudo descargar foto de {nombre}: {e}")

    timeline_personas = todos_integrantes if todos_integrantes else personas_meta
    html_content = _build_html(
        manuscript, nombre_familia, fotos, timeline_personas, relaciones or []
    )

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"/tmp/libro_{ts}.pdf"

    HTML(string=html_content).write_pdf(
        output_path,
        stylesheets=[CSS(string=CSS_BASE)],
    )
    return output_path
