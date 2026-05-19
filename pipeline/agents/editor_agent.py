"""
editor_agent.py — Genera la estructura narrativa del libro completo.

Recibe los capítulos ya escritos por chapter_agent y produce:
  - orden de lectura (cronológico por fecha de nacimiento)
  - transiciones entre capítulos (2-4 líneas, al cierre del anterior)
  - prólogo (300-400 palabras)
  - epílogo (200-300 palabras)

Las transiciones viven separadas de los capítulos — layout_agent las intercala.
Los capítulos nunca se modifican.

BookManuscript es el contrato de salida hacia layout_agent:
  prologo + orden + capitulos + transiciones + epilogo
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import anthropic

from utils.secrets import get_google_credentials, get_secret
from utils.sheets import SheetsClient

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class VoiceProfile:
    nombre: str
    tono: str
    frases_propias: list[str]
    citas_directas: list[str]
    detalles_sensoriales: list[str]
    fecha_nac: str


@dataclass
class ChapterInput:
    nombre: str
    capitulo: str


@dataclass
class BookStructure:
    orden: list[str]
    forward_refs: dict
    razonamiento: str


@dataclass
class BookManuscript:
    prologo: str
    orden: list[str]
    capitulos: dict           # {nombre: texto_capitulo}
    transiciones: dict        # {"nombre1->nombre2": texto_transicion}
    epilogo: str
    tokens_totales: int = 0


# ── System prompts ─────────────────────────────────────────────────────────────

SYSTEM_ORDEN = """\
Sos el arquitecto narrativo de un libro biográfico familiar digital.
Tu trabajo es definir el orden de lectura y detectar referencias cruzadas entre capítulos.

CRITERIOS DE ORDEN:
- Cronológico por fecha de nacimiento como regla absoluta
- Un día de diferencia igual define el orden — no hay empates
- Si dos personas nacieron el mismo día (mellizos exactos): el tono narrativo
  decide — el más anecdótico abre, el más reflexivo cierra
- Forward references: si en un capítulo aparece mencionado alguien que nació
  después y tiene capítulo propio, marcarlo para que la transición lo anticipe

FORMATO DE RESPUESTA — JSON puro, sin texto adicional, sin markdown:
{
  "orden": ["nombre1", "nombre2"],
  "forward_refs": {
    "nombre_que_menciona": ["nombre_mencionado1"]
  },
  "razonamiento": "una línea explicando la decisión más no obvia"
}"""


SYSTEM_TRANSICIONES = """\
Sos el escritor de las transiciones entre capítulos de un libro biográfico familiar digital.
Cada transición conecta dos personas reales.

REGLAS:
- Extensión: 2 a 4 líneas máximo. No más.
- Conectá usando el vocabulario propio de ambas personas — está en sus perfiles
- Si hay forward reference marcado, dejá la mención abierta, sin resolver:
  el lector va a conocer a esa persona en su propio capítulo más adelante
- Si las dos personas nacieron el mismo día o con un día de diferencia,
  la transición hace referencia explícita a esa cercanía de nacimiento
- Tono: narrativo, cálido, nunca administrativo
- Nunca uses: "a continuación", "en el siguiente capítulo", "ahora conoceremos"
- Nunca repitas información que ya está en los capítulos
- Devolvé solo el texto de la transición, sin título ni etiquetas

EJEMPLO BIEN EJECUTADO:
Elena siempre dijo que hablar rápido es hablar para uno.
Su hijo Carlos heredó el silencio — pero no la pausa.
El suyo es otro tipo de quietud.

EJEMPLO MAL EJECUTADO:
A continuación conoceremos la historia de Carlos, hijo de Elena,
quien tuvo una vida muy interesante llena de desafíos y aprendizajes."""


SYSTEM_PROLOGO_EPILOGO = """\
Sos el escritor de los extremos de un libro biográfico familiar digital.
Escribís el prólogo y el epílogo — los únicos textos que no hablan de una persona
específica sino del libro como objeto y de la familia como unidad.

PRÓLOGO:
- Extensión: 300 a 400 palabras
- No presenta a las personas — eso lo hacen los capítulos
- Responde una sola pregunta: por qué existe este libro
- La imagen de apertura debe venir del material real de los capítulos —
  no la inventes, encontrala en lo que ya está escrito
- Tono: como una carta al lector, no una introducción académica
- No reveles lo que viene — abrí una puerta, no mostrés el cuarto
- Español neutro latinoamericano — sin regionalismos

EPÍLOGO:
- Extensión: 200 a 300 palabras
- No resume los capítulos — el lector los acaba de leer
- La idea central es que la historia no cierra acá: estas personas siguieron
  viviendo después de que el grabador se apagó, y alguien que todavía no nació
  va a leer esto algún día
- Anclá esa idea en algo concreto que dijo alguien del libro — una frase, una imagen
- Puede dirigirse directamente a quien lo encuentre en el futuro
- Es el único lugar donde se puede romper la cuarta pared

REGLA PARA AMBOS:
Nunca uses las palabras: memorable, invaluable, legado, tesoro, generaciones,
entrañable, inmortal, huella — son los clichés más usados en este género."""


# ── Función principal ──────────────────────────────────────────────────────────

def run(nombres: list[str] | None = None) -> BookManuscript:
    """
    Lee capítulos desde Sheet "Perfiles" y produce el BookManuscript completo.
    """
    creds = get_google_credentials()
    sheets = SheetsClient(creds)
    client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    perfiles = sheets.get_perfiles()
    if not perfiles:
        raise RuntimeError("No hay perfiles en Sheet 'Perfiles'.")

    voice_profiles = []
    chapters = []

    for p in perfiles:
        nombre = p["nombre"].strip()
        if not nombre:
            continue
        if nombres and nombre not in nombres:
            continue
        if not p["capitulo"].strip():
            logger.warning("Sin capítulo para %s, saltando.", nombre)
            continue

        pv = {}
        if p["perfil_voz"].strip():
            try:
                pv = json.loads(p["perfil_voz"])
            except json.JSONDecodeError:
                logger.warning("perfil_voz inválido para %s.", nombre)

        fecha_nac = _get_fecha_nac(sheets, nombre)

        voice_profiles.append(VoiceProfile(
            nombre=nombre,
            tono=pv.get("tono", ""),
            frases_propias=pv.get("frases_propias", []),
            citas_directas=pv.get("citas_directas", []),
            detalles_sensoriales=pv.get("detalles_sensoriales", []),
            fecha_nac=fecha_nac,
        ))
        chapters.append(ChapterInput(nombre=nombre, capitulo=p["capitulo"]))

    if not chapters:
        raise RuntimeError("No hay capítulos disponibles para editar.")

    tokens = 0

    # Paso 1: orden narrativo
    estructura, t = _call_orden(client, voice_profiles, chapters)
    tokens += t
    logger.info("Orden definido: %s — %s", estructura.orden, estructura.razonamiento)

    # Paso 2: transiciones (una por par consecutivo)
    transiciones, t = _call_transiciones(client, voice_profiles, chapters, estructura)
    tokens += t
    logger.info("Transiciones generadas: %d", len(transiciones))

    # Paso 3: prólogo y epílogo
    prologo, epilogo, t = _call_prologo_epilogo(client, voice_profiles, chapters, estructura)
    tokens += t
    logger.info("Prólogo y epílogo generados.")

    capitulos_dict = {ch.nombre: ch.capitulo for ch in chapters}

    return BookManuscript(
        prologo=prologo,
        orden=estructura.orden,
        capitulos=capitulos_dict,
        transiciones=transiciones,
        epilogo=epilogo,
        tokens_totales=tokens,
    )


# ── Llamadas al modelo ─────────────────────────────────────────────────────────

def _call_orden(
    client: anthropic.Anthropic,
    voice_profiles: list[VoiceProfile],
    chapters: list[ChapterInput],
) -> tuple[BookStructure, int]:

    personas_info = [
        {
            "nombre": vp.nombre,
            "fecha_nacimiento": vp.fecha_nac,
            "tono": vp.tono,
            "frases_muestra": vp.frases_propias[:2],
        }
        for vp in voice_profiles
    ]

    capitulos_muestra = [
        {
            "nombre": ch.nombre,
            "muestra": " ".join(ch.capitulo.split()[:300]),
        }
        for ch in chapters
    ]

    user_prompt = f"""Definí el orden narrativo para este libro familiar.

PERSONAS (con fecha de nacimiento):
{json.dumps(personas_info, ensure_ascii=False, indent=2)}

MUESTRA DE CADA CAPÍTULO (primeras 300 palabras para detectar referencias):
{json.dumps(capitulos_muestra, ensure_ascii=False, indent=2)}

Devolvé el JSON según el formato indicado."""

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        temperature=0,  # orden es decisión lógica, sin variabilidad creativa
        system=SYSTEM_ORDEN,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    data = json.loads(raw)
    estructura = BookStructure(
        orden=data["orden"],
        forward_refs=data.get("forward_refs", {}),
        razonamiento=data.get("razonamiento", ""),
    )
    tokens = message.usage.input_tokens + message.usage.output_tokens
    return estructura, tokens


def _call_transiciones(
    client: anthropic.Anthropic,
    voice_profiles: list[VoiceProfile],
    chapters: list[ChapterInput],
    estructura: BookStructure,
) -> tuple[dict, int]:

    vp_map = {vp.nombre: vp for vp in voice_profiles}
    ch_map = {ch.nombre: ch for ch in chapters}
    pares = list(zip(estructura.orden[:-1], estructura.orden[1:]))

    def _generar_una(nombre_a: str, nombre_b: str) -> tuple[str, str, int]:
        vp_a = vp_map.get(nombre_a)
        vp_b = vp_map.get(nombre_b)
        ch_a = ch_map.get(nombre_a)
        forward = nombre_b in estructura.forward_refs.get(nombre_a, [])

        user_prompt = f"""Escribí la transición entre estos dos capítulos.

PERSONA QUE CIERRA: {nombre_a}
- Tono: {vp_a.tono if vp_a else '—'}
- Frases propias: {', '.join(vp_a.frases_propias[:3]) if vp_a else '—'}
- Fecha nacimiento: {vp_a.fecha_nac if vp_a else '—'}

PERSONA QUE ABRE: {nombre_b}
- Tono: {vp_b.tono if vp_b else '—'}
- Frases propias: {', '.join(vp_b.frases_propias[:3]) if vp_b else '—'}
- Fecha nacimiento: {vp_b.fecha_nac if vp_b else '—'}

CIERRE DEL CAPÍTULO ANTERIOR (últimas 200 palabras):
{" ".join(ch_a.capitulo.split()[-200:]) if ch_a else '—'}

{"NOTA: " + nombre_b + " fue mencionado en el capítulo anterior. Dejá esa mención abierta, sin resolver." if forward else ""}

Devolvé solo el texto de la transición."""

        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            temperature=1.0,  # escritura creativa — máxima expresividad
            system=SYSTEM_TRANSICIONES,
            messages=[{"role": "user", "content": user_prompt}],
        )
        clave = f"{nombre_a}->{nombre_b}"
        t = msg.usage.input_tokens + msg.usage.output_tokens
        return clave, msg.content[0].text.strip(), t

    # Transiciones en paralelo — son independientes entre sí
    transiciones = {}
    tokens = 0
    with ThreadPoolExecutor(max_workers=len(pares) or 1) as executor:
        futuros = {
            executor.submit(_generar_una, a, b): (a, b)
            for a, b in pares
        }
        for futuro in as_completed(futuros):
            clave, texto, t = futuro.result()
            transiciones[clave] = texto
            tokens += t
            logger.info("Transición generada: %s", clave)

    return transiciones, tokens


def _call_prologo_epilogo(
    client: anthropic.Anthropic,
    voice_profiles: list[VoiceProfile],
    chapters: list[ChapterInput],
    estructura: BookStructure,
) -> tuple[str, str, int]:

    ch_map = {ch.nombre: ch for ch in chapters}
    vp_map = {vp.nombre: vp for vp in voice_profiles}

    # Material concreto por persona en orden narrativo
    personas_material = []
    for nombre in estructura.orden:
        vp = vp_map.get(nombre)
        ch = ch_map.get(nombre)
        if not vp or not ch:
            continue
        palabras = ch.capitulo.split()
        personas_material.append({
            "nombre": nombre,
            "fecha_nac": vp.fecha_nac,
            "tono": vp.tono,
            # Apertura del capítulo — imagen inicial que engancha sin spoilear
            "apertura_capitulo": " ".join(palabras[:80]),
            # Cierre del capítulo — remate emocional
            "cierre_capitulo": " ".join(palabras[-120:]),
            # Frases en su propia voz — el material más específico disponible
            "citas_directas": vp.citas_directas[:3],
        })

    user_prompt = f"""Escribí el prólogo y el epílogo para este libro familiar.

PERSONAS DEL LIBRO (en orden narrativo, con material real de sus capítulos):
{json.dumps(personas_material, ensure_ascii=False, indent=2)}

INSTRUCCIÓN PARA EL PRÓLOGO:
Usá "apertura_capitulo" de cada persona para encontrar una imagen concreta
que enganche al lector sin revelar lo que viene. El prólogo no presenta a nadie —
abre una puerta. Buscá un hilo común entre las aperturas o tomá la más potente
como imagen inicial.

INSTRUCCIÓN PARA EL EPÍLOGO:
Usá "citas_directas" y "cierre_capitulo" para que el epílogo sea específico
a esta familia. La idea de que la historia continúa debe surgir de algo concreto
que dijeron estas personas — no de una reflexión abstracta sobre la memoria.

Devolvé un JSON con dos claves exactas: "prologo" y "epilogo".
Sin texto adicional, sin markdown. Solo el JSON."""

    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROLOGO_EPILOGO,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    data = json.loads(raw)
    tokens = message.usage.input_tokens + message.usage.output_tokens
    return data["prologo"], data["epilogo"], tokens


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_fecha_nac(sheets: SheetsClient, nombre: str) -> str:
    respuestas = sheets.get_respuestas()
    for r in respuestas:
        if r["nombre"].strip() == nombre and r.get("fecha_nac", "").strip():
            return r["fecha_nac"].strip()
    return ""


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    nombres_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    manuscript = run(nombres=nombres_arg)
    print(f"\nOrden: {manuscript.orden}")
    print(f"Transiciones: {list(manuscript.transiciones.keys())}")
    print(f"Tokens totales: {manuscript.tokens_totales}")
    print(f"\n── PRÓLOGO ──\n{manuscript.prologo[:300]}...")
    print(f"\n── EPÍLOGO ──\n{manuscript.epilogo[:300]}...")
