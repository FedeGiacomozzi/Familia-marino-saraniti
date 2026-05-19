"""
voice_agent.py — Extrae el perfil de voz completo de cada protagonista.

Lee las transcripciones del Sheet "Respuestas", agrupa por protagonista,
y pide a Claude que produzca:

  muletillas          palabras o frases cortas que repite frecuentemente
  frases_propias      expresiones características de esa persona
  registro            formalidad, humor, ritmo de oraciones
  detalles_sensoriales imágenes concretas mencionadas (olores, sonidos, texturas)
  tono                registro emocional general de la narrativa
  texto_limpio        la transcripción con las muletillas removidas

texto_limpio es lo que recibe chapter_agent: mismo contenido, mejor señal/ruido.
El orchestrator usa el perfil completo para armar PersonData.
"""

import json
import logging
import textwrap
from collections import defaultdict

import anthropic

from utils.secrets import get_google_credentials, get_secret
from utils.sheets import SheetsClient

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un lingüista especializado en oralidad del español latinoamericano.
    Tu rol es descriptivo, no editorial: analizás cómo habla esta persona específica,
    sin imponer ningún registro regional propio.
    Tu única tarea es extraer el perfil de voz de la persona entrevistada
    y devolverlo como JSON válido con exactamente seis campos.
""")

USER_PROMPT_TEMPLATE = textwrap.dedent("""\
    <transcripcion>
    {transcripcion}
    </transcripcion>

    Analizá el texto entre las etiquetas <transcripcion> y extraé los siguientes seis campos:

    1. muletillas
       Palabras o frases cortas que se repiten con frecuencia.
       Criterio: aparece 3 o más veces, O es tan característica que define la forma de hablar.
       Si no hay candidatos claros, devolvé [].

    2. frases_propias
       Expresiones más largas, metáforas, dichos o formas de iniciar frases únicas de esta persona.
       Si la transcripción es muy corta o no tiene expresiones distintivas, devolvé [].

    3. registro
       Objeto con tres claves exactas:
       - formalidad: "formal" | "informal" | "mixto"
       - humor: "ausente" | "sutil" | "frecuente, irónico" | "frecuente, sarcástico" |
                "frecuente, ingenuo" | "frecuente, cálido"
       - longitud_oraciones: "cortas" | "largas" | "mixtas"
         Incluí siempre una frase corta que describa el ritmo.
         Ejemplo: "mixtas, arranca despacio y acelera al emocionar"

    4. detalles_sensoriales
       Lista de imágenes concretas que la persona menciona: olores, sonidos, texturas,
       sabores, colores, temperaturas. Copiá las expresiones tal como aparecen en el texto.
       Ejemplos: "olor a leña mojada", "el sonido del tren a la madrugada",
                 "las paredes de adobe frío".
       Si no hay detalles sensoriales, devolvé [].

    5. tono
       Una frase de 5 a 15 palabras que describe el registro emocional predominante
       de esta narrativa. No uses adjetivos genéricos como "emotivo" o "interesante".
       Ejemplos: "nostálgico con momentos de orgullo familiar silencioso",
                 "alegre y acelerado, con melancolía al hablar de ausentes".

    6. citas_directas
       Frases de la transcripción que tienen suficiente personalidad para citarse
       textualmente en el capítulo biográfico. Copiá las frases exactas, sin modificarlas.
       Criterio: la frase revela algo genuino sobre quién es esa persona —
       una forma de ver el mundo, un momento vívido, una declaración memorable.
       No incluyas frases genéricas ni repetidas entre sí.
       Si no hay candidatas claras, devolvé [].

    7. texto_limpio
       La transcripción completa con las muletillas identificadas en el campo 1 removidas.
       No cambies ninguna otra palabra. Si muletillas es [], devolvé la transcripción sin cambios.

    Ejemplo de output esperado:
    {{
      "muletillas": ["o sea", "igual", "viste"],
      "frases_propias": ["en aquel entonces todo era distinto",
                         "te juro que sí, pero a mi manera"],
      "registro": {{
        "formalidad": "informal",
        "humor": "frecuente, irónico",
        "longitud_oraciones": "mixtas, oraciones cortas con pausas largas al recordar"
      }},
      "detalles_sensoriales": [
        "olor a pan recién horneado los domingos",
        "el frío del piso de mosaico descalzo"
      ],
      "tono": "nostálgico con orgullo contenido al hablar de los padres",
      "citas_directas": [
        "Yo nunca supe que era pobre hasta que salí del barrio.",
        "Mi vieja cocinaba con lo que había, pero siempre sobraba."
      ],
      "texto_limpio": "Yo nací en un pueblo pequeño... [transcripción sin muletillas]"
    }}

    IMPORTANTE: Respondé ÚNICAMENTE con el JSON.
    Sin texto antes, sin texto después, sin bloques markdown,
    sin comillas triples, sin comentarios. Solo el JSON.
""")


def run(nombres: list[str] | None = None) -> dict[str, dict]:
    """
    Procesa los protagonistas indicados (o todos si nombres=None).
    Devuelve dict {nombre: perfil_completo}.
    """
    creds = get_google_credentials()
    sheets = SheetsClient(creds)
    client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    respuestas = sheets.get_respuestas()
    if not respuestas:
        logger.warning("No hay filas en Sheet 'Respuestas'.")
        return {}

    transcripciones: dict[str, list[str]] = defaultdict(list)

    for row in respuestas:
        nombre = row["nombre"].strip()
        if not nombre:
            continue
        if nombres and nombre not in nombres:
            continue
        if row["transcripcion"].strip():
            transcripciones[nombre].append(row["transcripcion"].strip())

    if not transcripciones:
        logger.warning("No se encontraron transcripciones para procesar.")
        return {}

    resultados: dict[str, dict] = {}

    for nombre, textos in transcripciones.items():
        logger.info("Procesando voz de: %s (%d respuestas)", nombre, len(textos))

        transcripcion_completa = "\n\n---\n\n".join(textos)
        user_prompt = USER_PROMPT_TEMPLATE.format(transcripcion=transcripcion_completa)

        perfil = _extraer_perfil(client, user_prompt, nombre)
        resultados[nombre] = perfil

        sheets.upsert_perfil(
            nombre=nombre,
            perfil_voz=json.dumps(perfil, ensure_ascii=False),
            transcripcion=transcripcion_completa,
        )

    return resultados


def _extraer_perfil(client: anthropic.Anthropic, user_prompt: str, nombre: str) -> dict:
    for intento in range(2):
        message = client.messages.create(
            model=MODEL,
            max_tokens=4096,  # texto_limpio puede ser largo
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = message.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            perfil = json.loads(raw)
            _validar_perfil(perfil)
            logger.info("Perfil extraído OK para %s", nombre)
            return perfil
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Intento %d — JSON inválido para %s: %s", intento + 1, nombre, e)
            if intento == 0:
                user_prompt += (
                    "\n\nIMPORTANTE: el JSON anterior fue inválido. "
                    "Devolvé SOLO el JSON puro, sin ningún texto extra."
                )

    logger.error("JSON inválido tras 2 intentos para %s. Devolviendo perfil vacío.", nombre)
    return _perfil_vacio()


def _validar_perfil(perfil: dict) -> None:
    for campo in ["muletillas", "frases_propias", "registro",
                  "detalles_sensoriales", "tono", "citas_directas", "texto_limpio"]:
        if campo not in perfil:
            raise ValueError(f"Campo faltante: '{campo}'")
    for subcampo in ["formalidad", "humor", "longitud_oraciones"]:
        if subcampo not in perfil["registro"]:
            raise ValueError(f"Campo faltante en registro: '{subcampo}'")


def _perfil_vacio() -> dict:
    return {
        "muletillas": [],
        "frases_propias": [],
        "registro": {
            "formalidad": "no determinado",
            "humor": "no determinado",
            "longitud_oraciones": "no determinado",
        },
        "detalles_sensoriales": [],
        "tono": "no determinado",
        "citas_directas": [],
        "texto_limpio": "",
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    nombres_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    resultado = run(nombres=nombres_arg)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
