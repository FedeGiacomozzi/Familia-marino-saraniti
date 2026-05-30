"""
Generador de árbol genealógico SVG.
Toma la lista de integrantes y relaciones de Firestore y produce un SVG embebible.

Uso:
    from pipeline.utils.tree import generar_arbol_svg
    svg = generar_arbol_svg(integrantes, relaciones, nombre_familia)
"""

from __future__ import annotations
import math
from typing import Any


# ── Constantes de layout ─────────────────────────────────────────────────────

NODE_W   = 140
NODE_H   = 64
H_GAP    = 40   # espacio horizontal entre nodos hermanos
V_GAP    = 90   # espacio vertical entre generaciones
FONT     = "Playfair Display, Georgia, serif"
FONT_SM  = "Lato, Arial, sans-serif"
BG       = "#fdf8f2"
NODE_BG  = "#fff"
NODE_BRD = "#e8d9b8"
NAME_CLR = "#3d2b0a"
DATE_CLR = "#9a7b5a"
LINE_CLR = "#c4a882"
COUPLE_CLR = "#d4946a"


# ── Modelos internos ─────────────────────────────────────────────────────────

class Persona:
    def __init__(self, nombre: str, fecha_nac: str = "", fecha_fallec: str = "",
                 rol: str = "", es_menor: bool = False):
        self.nombre      = nombre
        self.fecha_nac   = fecha_nac
        self.fecha_fallec = fecha_fallec
        self.rol         = rol
        self.es_menor    = es_menor
        self.pareja: str | None   = None
        self.padres: list[str]    = []
        self.hijos: list[str]     = []
        # posición asignada en el SVG
        self.x: float = 0
        self.y: float = 0
        self.gen: int = 0   # generación (0 = raíz/compradores)


# ── Parser de datos Firestore ─────────────────────────────────────────────────

def _parsear(integrantes: list[dict], relaciones: list[dict]) -> dict[str, Persona]:
    personas: dict[str, Persona] = {}
    for ing in integrantes:
        nombre = ing["nombre"].strip()
        if not nombre:
            continue
        personas[nombre] = Persona(
            nombre      = nombre,
            fecha_nac   = ing.get("fecha_nac", ""),
            fecha_fallec= ing.get("fecha_fallec", ""),
            rol         = ing.get("rol", ""),
            es_menor    = bool(ing.get("es_menor", False)),
        )

    for rel in relaciones:
        a   = rel.get("persona_a", "").strip()
        r   = rel.get("relacion", "").strip().lower()
        b   = rel.get("persona_b", "").strip()
        if not (a and b):
            continue

        if r in ("cónyuge", "conyuge", "pareja", "esposo", "esposa"):
            if a in personas:
                personas[a].pareja = b
            if b in personas:
                personas[b].pareja = a

        elif r in ("padre", "madre", "progenitor"):
            # a es padre/madre de b
            if b in personas and a not in personas[b].padres:
                personas[b].padres.append(a)
            if a in personas and b not in personas[a].hijos:
                personas[a].hijos.append(b)

        elif r in ("hijo", "hija"):
            # a es hijo/hija de b → b es padre de a
            if a in personas and b not in personas[a].padres:
                personas[a].padres.append(b)
            if b in personas and a not in personas[b].hijos:
                personas[b].hijos.append(a)

    return personas


# ── Asignación de generaciones ───────────────────────────────────────────────

def _asignar_generaciones(personas: dict[str, Persona]) -> None:
    # Nodos raíz = sin padres
    raices = [p for p in personas.values() if not p.padres]
    if not raices:
        raices = list(personas.values())[:1]

    visitados: set[str] = set()

    def dfs(nombre: str, gen: int):
        if nombre in visitados:
            return
        visitados.add(nombre)
        p = personas[nombre]
        p.gen = max(p.gen, gen)
        for hijo in p.hijos:
            if hijo in personas:
                dfs(hijo, gen + 1)

    for r in raices:
        dfs(r.nombre, 0)

    # Asegurar que todos tengan generación asignada
    for p in personas.values():
        if p.nombre not in visitados:
            p.gen = 0


# ── Layout Sugiyama simplificado ─────────────────────────────────────────────

def _layout(personas: dict[str, Persona]) -> None:
    _asignar_generaciones(personas)

    # Agrupar por generación
    gens: dict[int, list[Persona]] = {}
    for p in personas.values():
        gens.setdefault(p.gen, []).append(p)

    # Colocar parejas juntas dentro de cada generación
    def _ordenar_gen(ps: list[Persona]) -> list[Persona]:
        placed  = set()
        ordered = []
        for p in ps:
            if p.nombre in placed:
                continue
            ordered.append(p)
            placed.add(p.nombre)
            if p.pareja and p.pareja in {x.nombre for x in ps} and p.pareja not in placed:
                pareja = next(x for x in ps if x.nombre == p.pareja)
                ordered.append(pareja)
                placed.add(p.pareja)
        return ordered

    max_gen = max(gens.keys(), default=0)

    for gen_idx in range(max_gen + 1):
        ps = _ordenar_gen(gens.get(gen_idx, []))
        total_w = len(ps) * NODE_W + max(0, len(ps) - 1) * H_GAP
        start_x = -total_w / 2
        y = gen_idx * (NODE_H + V_GAP)
        for i, p in enumerate(ps):
            p.x = start_x + i * (NODE_W + H_GAP) + NODE_W / 2
            p.y = y


# ── Renderizado SVG ───────────────────────────────────────────────────────────

def _rect_persona(p: Persona) -> str:
    x  = p.x - NODE_W / 2
    y  = p.y
    # Color de fondo según rol
    bg = NODE_BG
    if "bisabuelo" in p.rol or "tatarabuelo" in p.rol:
        bg = "#f5f0e8"
    elif "abuelo" in p.rol or "abuela" in p.rol:
        bg = "#faf5ec"
    elif p.es_menor:
        bg = "#f0f8ff"

    # Nombre abreviado si es muy largo
    nombre_display = p.nombre
    if len(nombre_display) > 18:
        partes = nombre_display.split()
        nombre_display = partes[0] + (" " + partes[-1] if len(partes) > 1 else "")

    # Fechas
    fechas = ""
    if p.fecha_nac:
        anno = p.fecha_nac[:4] if len(p.fecha_nac) >= 4 else p.fecha_nac
        if p.fecha_fallec:
            anno_f = p.fecha_fallec[:4] if len(p.fecha_fallec) >= 4 else p.fecha_fallec
            fechas = f"{anno} – {anno_f}"
        else:
            fechas = f"n. {anno}"

    dagger = " †" if p.fecha_fallec else ""

    return f"""
  <g>
    <rect x="{x:.1f}" y="{p.y:.1f}" width="{NODE_W}" height="{NODE_H}"
          rx="8" fill="{bg}" stroke="{NODE_BRD}" stroke-width="0.5"/>
    <text x="{p.x:.1f}" y="{p.y + 26:.1f}" text-anchor="middle"
          font-family="{FONT}" font-size="13" fill="{NAME_CLR}" font-weight="500">
      {_esc(nombre_display)}{dagger}
    </text>
    <text x="{p.x:.1f}" y="{p.y + 44:.1f}" text-anchor="middle"
          font-family="{FONT_SM}" font-size="10" fill="{DATE_CLR}">
      {_esc(fechas) if fechas else _esc(p.rol.capitalize())}
    </text>
  </g>"""


def _linea_pareja(pa: Persona, pb: Persona) -> str:
    """Línea horizontal entre dos parejas."""
    y = pa.y + NODE_H / 2
    x1 = min(pa.x, pb.x) + NODE_W / 2
    x2 = max(pa.x, pb.x) - NODE_W / 2
    mid = (x1 + x2) / 2
    return f"""
  <line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}"
        stroke="{COUPLE_CLR}" stroke-width="1.5" stroke-dasharray="4 3"/>
  <circle cx="{mid:.1f}" cy="{y:.1f}" r="4" fill="{COUPLE_CLR}" opacity="0.7"/>"""


def _lineas_hijos(padre: Persona, hijos: list[Persona]) -> str:
    if not hijos:
        return ""
    svgs = []
    px = padre.x
    py = padre.y + NODE_H
    # Punto de bajada desde el padre
    mid_y = py + V_GAP * 0.45

    # Si hay pareja, el punto de partida es el centro entre los dos
    if padre.pareja:
        # El conector ya se hizo desde el centro de la línea de pareja
        pass

    for hijo in hijos:
        hx = hijo.x
        hy = hijo.y
        svgs.append(f"""
  <path d="M {px:.1f} {py:.1f} L {px:.1f} {mid_y:.1f} L {hx:.1f} {mid_y:.1f} L {hx:.1f} {hy:.1f}"
        fill="none" stroke="{LINE_CLR}" stroke-width="1"/>""")
    return "".join(svgs)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── Función pública ───────────────────────────────────────────────────────────

def generar_arbol_svg(
    integrantes: list[dict],
    relaciones: list[dict],
    nombre_familia: str = "",
    width: int = 900,
) -> str:
    """
    Genera un SVG del árbol genealógico.
    Retorna el string SVG completo, listo para embeber en HTML o PDF.
    """
    if not integrantes:
        return _svg_vacio(nombre_familia, width)

    personas = _parsear(integrantes, relaciones)
    if not personas:
        return _svg_vacio(nombre_familia, width)

    _layout(personas)

    # Calcular bounding box
    xs = [p.x for p in personas.values()]
    ys = [p.y for p in personas.values()]
    min_x = min(xs) - NODE_W / 2 - 40
    min_y = min(ys) - 40
    max_x = max(xs) + NODE_W / 2 + 40
    max_y = max(ys) + NODE_H + 40

    vw = max_x - min_x
    vh = max_y - min_y
    svg_w = max(width, int(vw))
    svg_h = int(vh) + (60 if nombre_familia else 0)
    cx = (min_x + max_x) / 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{min_x:.0f} {min_y:.0f} {vw:.0f} {vh:.0f}" '
        f'width="{svg_w}" height="{svg_h}" '
        f'style="background:{BG};font-family:{FONT_SM};max-width:100%;height:auto">',
    ]

    # Título
    if nombre_familia:
        parts.append(
            f'<text x="{cx:.1f}" y="{min_y + 28:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="16" fill="{NAME_CLR}" font-weight="500">'
            f'Árbol genealógico · {_esc(nombre_familia)}</text>'
        )

    # Líneas de pareja (antes de los nodos para que queden detrás)
    ya_dibujadas: set[frozenset] = set()
    for p in personas.values():
        if p.pareja and p.pareja in personas:
            key = frozenset([p.nombre, p.pareja])
            if key not in ya_dibujadas:
                ya_dibujadas.add(key)
                parts.append(_linea_pareja(p, personas[p.pareja]))

    # Líneas padre→hijos
    for p in personas.values():
        hijos_objs = [personas[h] for h in p.hijos if h in personas]
        if hijos_objs:
            parts.append(_lineas_hijos(p, hijos_objs))

    # Nodos de personas
    for p in personas.values():
        parts.append(_rect_persona(p))

    parts.append("</svg>")
    return "\n".join(parts)


def _svg_vacio(nombre_familia: str, width: int) -> str:
    msg = f"Árbol de la {nombre_familia}" if nombre_familia else "Árbol genealógico"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="120" '
        f'style="background:{BG}">'
        f'<text x="{width//2}" y="64" text-anchor="middle" '
        f'font-family="{FONT}" font-size="16" fill="{DATE_CLR}">{_esc(msg)}</text>'
        f"</svg>"
    )


# ── CLI helper ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Prueba básica
    ings = [
        {"nombre": "Carlos García", "fecha_nac": "1945", "rol": "abuelo"},
        {"nombre": "Rosa García",   "fecha_nac": "1948", "rol": "abuela"},
        {"nombre": "Martín García", "fecha_nac": "1972", "rol": "padre"},
        {"nombre": "Laura García",  "fecha_nac": "1974", "rol": "madre"},
        {"nombre": "Sofía García",  "fecha_nac": "2001", "rol": "hija"},
        {"nombre": "Tomás García",  "fecha_nac": "2003", "rol": "hijo", "es_menor": True},
    ]
    rels = [
        {"persona_a": "Carlos García", "relacion": "cónyuge", "persona_b": "Rosa García"},
        {"persona_a": "Martín García", "relacion": "cónyuge", "persona_b": "Laura García"},
        {"persona_a": "Carlos García", "relacion": "padre",   "persona_b": "Martín García"},
        {"persona_a": "Rosa García",   "relacion": "madre",   "persona_b": "Martín García"},
        {"persona_a": "Martín García", "relacion": "padre",   "persona_b": "Sofía García"},
        {"persona_a": "Laura García",  "relacion": "madre",   "persona_b": "Sofía García"},
        {"persona_a": "Martín García", "relacion": "padre",   "persona_b": "Tomás García"},
        {"persona_a": "Laura García",  "relacion": "madre",   "persona_b": "Tomás García"},
    ]
    svg = generar_arbol_svg(ings, rels, "Familia García")
    with open("/tmp/tree_test.svg", "w") as f:
        f.write(svg)
    print("SVG escrito en /tmp/tree_test.svg")
