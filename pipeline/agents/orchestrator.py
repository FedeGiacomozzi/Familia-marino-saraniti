"""
orchestrator.py — Coordina el pipeline completo de arriba a abajo.

Orden de ejecución:
  1. transcriber   audios pendientes → texto raw en Sheet "Respuestas"
  2. voice_agent   transcripciones → perfil de voz + texto_limpio en Sheet "Perfiles"
  3. chapters      PersonData por protagonista → capítulos en paralelo
  4. editor_agent  capítulos → intro + transiciones + cierre  [stub]
  5. layout_agent  HTML A5 → PDF final                        [stub]

main.py delega toda la lógica de pipeline a este módulo.
Los endpoints individuales (/run/transcriber, /run/voice, etc.) siguen
disponibles para correr pasos sueltos durante desarrollo.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import anthropic

from utils.secrets import get_google_credentials, get_secret
from utils.sheets import SheetsClient

logger = logging.getLogger(__name__)

MAX_WORKERS = 4  # capítulos en paralelo — ajustar según rate limits


@dataclass
class PersonData:
    nombre: str
    fecha_nac: str
    transcripcion_raw: str
    texto_limpio: str
    muletillas: list[str]
    frases_propias: list[str]
    registro: dict
    detalles_sensoriales: list[str]
    tono: str
    citas_directas: list[str]
    capitulo: str = field(default="")


@dataclass
class PipelineResult:
    personas: list[PersonData]
    transcriber: dict
    voice: dict
    chapters: dict
    editor: dict
    layout: dict
    errores: list[str] = field(default_factory=list)
    _manuscript: object = field(default=None, repr=False)  # BookManuscript, opcional


def run(
    nombres: list[str] | None = None,
    pais: str | None = None,
    solo_desde: str | None = None,
    familia: str = "Familia",
    upload_to_drive: bool = False,
) -> PipelineResult:
    """
    Corre el pipeline completo o desde un paso específico.

    nombres:    filtra protagonistas. None = todos.
    pais:       hint regional para Whisper (ej: "argentina"). None = genérico.
    solo_desde: "voice" | "chapters" | "editor" | "layout"
                Saltea los pasos anteriores (útil cuando ya están procesados).
    """
    result = PipelineResult(
        personas=[], transcriber={}, voice={}, chapters={}, editor={}, layout={}
    )

    pasos = ["transcriber", "voice", "chapters", "editor", "layout"]
    inicio = pasos.index(solo_desde) if solo_desde in pasos else 0

    creds = get_google_credentials()
    sheets = SheetsClient(creds)

    # ── Paso 1: transcripción ──────────────────────────────────────────────────
    if inicio <= 0:
        logger.info("── Paso 1: transcriber ──")
        try:
            from agents.transcriber import run as _transcriber
            result.transcriber = _transcriber(pais=pais)
            logger.info("Transcriber OK: %s", result.transcriber)
        except Exception as e:
            logger.error("Transcriber falló: %s", e)
            result.transcriber = {"error": str(e)}
            result.errores.append(f"transcriber: {e}")

    # ── Paso 2: voice agent ────────────────────────────────────────────────────
    if inicio <= 1:
        logger.info("── Paso 2: voice_agent ──")
        try:
            from agents.voice_agent import run as _voice
            voice_result = _voice(nombres=nombres)
            result.voice = {"protagonistas": list(voice_result.keys())}
            logger.info("Voice OK: %s", list(voice_result.keys()))
        except Exception as e:
            logger.error("Voice falló: %s", e)
            result.voice = {"error": str(e)}
            result.errores.append(f"voice: {e}")

    # ── Paso 3: capítulos en paralelo ─────────────────────────────────────────
    if inicio <= 2:
        logger.info("── Paso 3: chapters (paralelo) ──")
        try:
            personas = _cargar_personas(sheets, nombres)
            if not personas:
                logger.warning("Sin perfiles completos para generar capítulos.")
            else:
                personas = _generar_capitulos_paralelo(personas)
                for persona in personas:
                    if persona.capitulo:
                        sheets.upsert_perfil(nombre=persona.nombre, capitulo=persona.capitulo)
            result.personas = personas
            result.chapters = {
                "protagonistas": [p.nombre for p in personas],
                "palabras": {p.nombre: len(p.capitulo.split()) for p in personas if p.capitulo},
            }
            logger.info("Chapters OK: %s", result.chapters)
        except Exception as e:
            logger.error("Chapters falló: %s", e)
            result.chapters = {"error": str(e)}
            result.errores.append(f"chapters: {e}")

    # ── Paso 4: editor ────────────────────────────────────────────────────────
    if inicio <= 3:
        logger.info("── Paso 4: editor_agent ──")
        try:
            from agents.editor_agent import run as _editor
            manuscript = _editor(nombres=nombres)
            result.editor = {
                "orden": manuscript.orden,
                "transiciones": list(manuscript.transiciones.keys()),
                "tokens": manuscript.tokens_totales,
            }
            # Guardar manuscript en result para que layout lo consuma
            result._manuscript = manuscript
            logger.info("Editor OK: orden=%s", manuscript.orden)
        except Exception as e:
            logger.error("Editor falló: %s", e)
            result.editor = {"error": str(e)}
            result.errores.append(f"editor: {e}")

    # ── Paso 5: layout ────────────────────────────────────────────────────────
    if inicio <= 4:
        logger.info("── Paso 5: layout_agent ──")
        manuscript = getattr(result, "_manuscript", None)
        if not manuscript:
            logger.warning("Sin manuscript del editor, saltando layout.")
            result.layout = {"status": "saltado — editor no completó"}
        else:
            try:
                from agents.layout_agent import run as _layout
                pdf_path = _layout(
                    manuscript=manuscript,
                    familia=familia,
                    upload_to_drive=upload_to_drive,
                )
                result.layout = {"pdf": pdf_path}
                logger.info("Layout OK: %s", pdf_path)
            except Exception as e:
                logger.error("Layout falló: %s", e)
                result.layout = {"error": str(e)}
                result.errores.append(f"layout: {e}")

    logger.info("Pipeline finalizado. Errores: %d", len(result.errores))
    return result


# ── Helpers internos ───────────────────────────────────────────────────────────

def _cargar_personas(sheets: SheetsClient, nombres: list[str] | None) -> list[PersonData]:
    """Construye PersonData desde Sheet 'Perfiles'. Solo incluye perfiles completos."""
    perfiles = sheets.get_perfiles()
    fecha_nac_map = _get_fechas_nacimiento(sheets)

    personas = []
    for p in perfiles:
        nombre = p["nombre"].strip()
        if not nombre:
            continue
        if nombres and nombre not in nombres:
            continue
        if not p["perfil_voz"].strip() or not p["transcripcion"].strip():
            logger.warning("Perfil incompleto, saltando: %s", nombre)
            continue

        try:
            pv = json.loads(p["perfil_voz"])
        except json.JSONDecodeError:
            logger.error("perfil_voz JSON inválido para %s, saltando.", nombre)
            continue

        personas.append(PersonData(
            nombre=nombre,
            fecha_nac=fecha_nac_map.get(nombre, ""),
            transcripcion_raw=p["transcripcion"],
            texto_limpio=pv.get("texto_limpio") or p["transcripcion"],
            muletillas=pv.get("muletillas", []),
            frases_propias=pv.get("frases_propias", []),
            registro=pv.get("registro", {}),
            detalles_sensoriales=pv.get("detalles_sensoriales", []),
            tono=pv.get("tono", ""),
            citas_directas=pv.get("citas_directas", []),
        ))

    return personas


def _generar_capitulos_paralelo(personas: list[PersonData]) -> list[PersonData]:
    from agents.chapter_agent import generar_capitulo

    client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(personas))) as executor:
        futuros = {
            executor.submit(generar_capitulo, client, persona): persona
            for persona in personas
        }
        for futuro in as_completed(futuros):
            persona = futuros[futuro]
            try:
                persona.capitulo = futuro.result()
                logger.info("Capítulo listo: %s — %d palabras",
                            persona.nombre, len(persona.capitulo.split()))
            except Exception as e:
                logger.error("Error capítulo %s: %s", persona.nombre, e)
                persona.capitulo = ""

    return personas


def _get_fechas_nacimiento(sheets: SheetsClient) -> dict[str, str]:
    respuestas = sheets.get_respuestas()
    result: dict[str, str] = {}
    for r in respuestas:
        nombre = r["nombre"].strip()
        if nombre and nombre not in result and r.get("fecha_nac", "").strip():
            result[nombre] = r["fecha_nac"].strip()
    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Uso: python orchestrator.py [--desde PASO] [--pais PAIS] [nombre1 nombre2 ...]
    # Ej:  python orchestrator.py --desde chapters --pais argentina
    args = sys.argv[1:]
    pais_arg = None
    desde_arg = None
    nombres_arg = []

    i = 0
    while i < len(args):
        if args[i] == "--pais" and i + 1 < len(args):
            pais_arg = args[i + 1]; i += 2
        elif args[i] == "--desde" and i + 1 < len(args):
            desde_arg = args[i + 1]; i += 2
        else:
            nombres_arg.append(args[i]); i += 1

    pipeline = run(
        nombres=nombres_arg or None,
        pais=pais_arg,
        solo_desde=desde_arg,
    )
    print(f"\nTranscriber : {pipeline.transcriber}")
    print(f"Voice       : {pipeline.voice}")
    print(f"Chapters    : {pipeline.chapters}")
    print(f"Editor      : {pipeline.editor}")
    print(f"Layout      : {pipeline.layout}")
    if pipeline.errores:
        print(f"\nErrores     : {pipeline.errores}")
