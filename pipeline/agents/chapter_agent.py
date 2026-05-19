"""
chapter_agent.py — Genera el capítulo biográfico de un protagonista.

Recibe un PersonData ya armado por orchestrator.py (no lee Sheet directamente).
La función pública es generar_capitulo(client, persona) → str.

La voz del protagonista aparece en cursiva, integrada en la narrativa.
Nunca entre comillas, nunca en primera persona directa.
"""

import json
import logging
import textwrap

import anthropic

from utils.secrets import get_google_credentials, get_secret
from utils.sheets import SheetsClient

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 6000

SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un escritor y editor literario especializado en libros biográficos familiares
    en español latinoamericano.
    Tu única tarea en este mensaje es escribir un capítulo narrativo sobre el protagonista
    usando los datos provistos.
""")

USER_PROMPT_TEMPLATE = textwrap.dedent("""\
    <nombre>
    {nombre}
    </nombre>

    <fecha_nacimiento>
    {fecha_nacimiento}
    </fecha_nacimiento>

    <perfil_voz>
    {perfil_voz_json}
    </perfil_voz>

    <transcripcion>
    {transcripcion}
    </transcripcion>

    ---

    CÓMO USAR CADA INPUT

    La <transcripcion> es tu materia prima. Todo lo que escribís debe poder rastrearse
    a algo que la persona dijo o describió. No añadís hechos, emociones ni contextos
    que no estén ahí.

    El <perfil_voz> es tu brújula tonal. Usalo así:
    - formalidad → ajustá el nivel de lengua del narrador
    - humor → si es frecuente, dejá que el capítulo respire;
      si es ausente, mantené el tono sin forzar ligereza
    - longitud_oraciones → usá ese ritmo como referencia
      para construir párrafos que suenen a esa persona
    - frases_propias → incorporalas en cursiva, sin comillas,
      integradas naturalmente en la narrativa — máximo 2 o 3
    - muletillas → ignoralas completamente; son del oral,
      no del texto escrito

    ---

    PRINCIPIOS DE ESCRITURA

    - Narrador en tercera persona literaria: escribís sobre
      {nombre}, no como {nombre}
    - El protagonista nunca habla en primera persona ni entre
      comillas — su voz aparece solo a través de las cursivas
    - Tono cálido, íntimo y respetuoso — sin solemnidad excesiva
    - El detalle concreto vale más que la generalización
    - Tiempo verbal base: pasado narrativo, excepto para
      estados presentes o reflexiones actuales del protagonista
    - Párrafos de longitud variada para crear ritmo
    - Sin listas, sin subtítulos, sin secciones numeradas
    - Extensión: entre 3200 y 3800 palabras

    ARCO NARRATIVO SUGERIDO
    No es obligatorio, pero orientá el capítulo así:
    1. Apertura con una imagen o momento específico que
       ancle al lector en la vida de {nombre}
    2. Desarrollo cronológico o temático según lo que
       la transcripción permita
    3. Cierre que no resuma sino que deje una imagen,
       frase o sensación final

    ---

    IMPORTANTE: No inventés lo que la transcripción no dice.
    Si falta información para completar una sección,
    desarrollá más en profundidad lo que sí está.
    Las muletillas del habla oral no aparecen nunca en el texto escrito.
    El protagonista no habla nunca entre comillas.
    Su voz solo aparece integrada en cursiva.
""")


def generar_capitulo(client: anthropic.Anthropic, persona) -> str:
    """
    Genera el capítulo para un PersonData. Función pública llamada por orchestrator.
    Reintenta una vez si el resultado es muy corto.
    """
    perfil_voz_json = json.dumps(
        {
            "muletillas":           persona.muletillas,
            "frases_propias":       persona.frases_propias,
            "registro":             persona.registro,
            "detalles_sensoriales": persona.detalles_sensoriales,
            "tono":                 persona.tono,
            "citas_directas":       persona.citas_directas,
        },
        ensure_ascii=False,
        indent=2,
    )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        nombre=persona.nombre,
        fecha_nacimiento=persona.fecha_nac or "no especificada",
        perfil_voz_json=perfil_voz_json,
        transcripcion=persona.texto_limpio or persona.transcripcion_raw,
    )

    for intento in range(2):
        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        capitulo = message.content[0].text.strip()
        palabras = len(capitulo.split())

        if palabras >= 2000:
            return capitulo

        logger.warning(
            "Capítulo corto para %s: %d palabras (intento %d).",
            persona.nombre, palabras, intento + 1,
        )
        if intento == 0:
            user_prompt += (
                f"\n\nIMPORTANTE: el capítulo debe tener entre 3200 y 3800 palabras. "
                f"El anterior tuvo solo {palabras}. "
                f"Desarrollá más en profundidad lo que la transcripción permite."
            )

    return capitulo


# ── Modo standalone (sin orchestrator) ────────────────────────────────────────

def run(nombres: list[str] | None = None) -> dict[str, str]:
    """Wrapper standalone: lee Sheet y genera capítulos sin orchestrator."""
    from agents.orchestrator import _cargar_personas

    creds = get_google_credentials()
    sheets = SheetsClient(creds)
    client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    personas = _cargar_personas(sheets, nombres)
    if not personas:
        logger.warning("No hay perfiles completos.")
        return {}

    resultados = {}
    for persona in personas:
        capitulo = generar_capitulo(client, persona)
        resultados[persona.nombre] = capitulo
        sheets.upsert_perfil(nombre=persona.nombre, capitulo=capitulo)
        logger.info("Capítulo guardado: %s — %d palabras", persona.nombre, len(capitulo.split()))

    return resultados


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    nombres_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    resultado = run(nombres=nombres_arg)
    for nombre, cap in resultado.items():
        print(f"\n{'='*60}\n  {nombre} — {len(cap.split())} palabras\n{'='*60}")
        print(cap[:500] + "...")
