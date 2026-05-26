"""
Lingüista descriptivo: analiza las transcripciones de cada persona
y construye un perfil de voz JSON de 7 campos.
"""

import json
import re
from datetime import datetime

import anthropic

from pipeline.utils import firestore as fstore

MODEL = "claude-opus-4-7"

_SYSTEM = """\
Sos un lingüista descriptivo especializado en oralidad latinoamericana.
Tu trabajo es registrar — no corregir ni juzgar — cómo habla una persona.
Describís su voz escrita con precisión etnográfica.
"""

_PROMPT_TEMPLATE = """\
Analizá las siguientes transcripciones orales de {nombre}.

<transcripciones>
{bloques}
</transcripciones>

Devolvé EXCLUSIVAMENTE un JSON válido con estos 7 campos:

{{
  "muletillas": ["lista de muletillas y palabras de relleno que usa habitualmente"],
  "frases_propias": ["frases o expresiones características que usa más de una vez o que lo/la identifican"],
  "registro": "descripción del registro lingüístico: formal/informal/coloquial/técnico/mixto, con ejemplos",
  "detalles_sensoriales": ["imágenes, metáforas, referencias concretas al cuerpo, al espacio, a los sentidos"],
  "tono": "descripción del tono emocional predominante y sus variaciones",
  "citas_directas": ["5 a 8 fragmentos literales especialmente expresivos o reveladores, mínimo 20 palabras cada uno"],
  "texto_limpio": "toda la transcripción unificada, sin indicadores de pregunta, como un monólogo continuo"
}}

Solo JSON. Sin explicaciones. Sin markdown.
"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _analyze_persona(client: anthropic.Anthropic, nombre: str) -> dict:
    transcripciones = fstore.get_transcripciones(nombre)
    if not transcripciones:
        raise ValueError(f"No hay transcripciones para {nombre}")

    bloques = "\n\n".join(
        f"[Pregunta {t['pregunta']}]\n{t['transcripcion']}"
        for t in transcripciones
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": _PROMPT_TEMPLATE.format(nombre=nombre, bloques=bloques),
            }
        ],
    )

    perfil = _parse_json_response(message.content[0].text)

    transcripcion_completa = "\n\n".join(t["transcripcion"] for t in transcripciones)
    fecha_process = datetime.now().strftime("%d/%m/%Y %H:%M")
    fstore.save_profile(nombre, fecha_process, json.dumps(perfil, ensure_ascii=False), transcripcion_completa)

    return perfil


def run(nombres: list[str]) -> dict[str, dict]:
    """
    Analyze each persona and return {nombre: perfil_dict}.
    Saves results to the Perfiles sheet.
    """
    client = anthropic.Anthropic()
    results = {}
    for nombre in nombres:
        try:
            results[nombre] = _analyze_persona(client, nombre)
        except Exception as e:
            print(f"[voice_agent] Error con {nombre}: {e}")
            results[nombre] = {"error": str(e)}
    return results
