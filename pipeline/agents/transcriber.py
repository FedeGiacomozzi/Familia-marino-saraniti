"""
transcriber.py — Transcribe audios con Whisper API y los escribe en el Sheet.

Lee filas del Sheet "Respuestas" que tengan link de audio pero sin transcripción,
descarga cada archivo desde Drive, llama a Whisper y escribe el resultado en col F.

Whisper recibe el archivo de audio directamente (no base64).
Límite de Whisper: 25 MB por archivo.

El parámetro `pais` genera un hint de vocabulario para Whisper calibrado por país,
sin atar la transcripción a una familia ni región específica.
"""

import io
import logging
import re
import time
from urllib.parse import urlparse, parse_qs

import openai
import requests

from utils.secrets import get_google_credentials, get_secret
from utils.sheets import SheetsClient

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-1"
WHISPER_LANGUAGE = "es"
MAX_RETRIES = 3
RETRY_DELAY = 5  # segundos

# Hints de vocabulario por país para Whisper.
# Ayudan a Whisper a reconocer correctamente palabras regionales que acústicamente
# podrían confundirse o transcribirse mal. Whisper no inserta palabras que no se
# dijeron: solo mejora su reconocimiento cuando sí se dicen.
# voice_agent luego analiza la transcripción resultante para identificar qué
# palabras son efectivamente características de esa persona.
_WHISPER_HINTS: dict[str, str] = {
    "argentina":  "Relato familiar en español rioplatense. Vocabulario frecuente: vos, che, pibe, laburo, quilombo, re, igual, tipo, dale, boludo, mina, plata, boliche, colectivo, heladera.",
    "uruguay":    "Relato familiar en español rioplatense uruguayo. Vocabulario frecuente: vos, che, ta, gurí, fachero, copado, bárbaro, buenazo, hincha, cuadro, ómnibus.",
    "chile":      "Relato familiar en español chileno. Vocabulario frecuente: po, cachai, weon, al tiro, fome, bacán, pololo, pololear, plata, micro, cabro, caleta.",
    "colombia":   "Relato familiar en español colombiano. Vocabulario frecuente: parce, bacano, chévere, vaina, marica, listo, parcero, plata, billete, man, chimbo, rumba.",
    "mexico":     "Relato familiar en español mexicano. Vocabulario frecuente: órale, güey, chido, ahorita, mande, híjole, chamba, lana, cuate, padrísimo, neta, chavo.",
    "españa":     "Relato familiar en español peninsular. Vocabulario frecuente: tío, mola, guay, joder, venga, vale, chaval, curro, pasta, piso, coche, móvil.",
    "venezuela":  "Relato familiar en español venezolano. Vocabulario frecuente: chamo, pana, chévere, coño, broma, ladilla, arrecho, marico, plata, cachifa, guarandinga.",
    "peru":       "Relato familiar en español peruano. Vocabulario frecuente: pata, causa, pe, al toque, bacán, jerma, plata, causa, jato, palta, chibolo, mostro.",
    "default":    "Relato familiar en español latinoamericano.",
}


def _whisper_hint(pais: str | None) -> str:
    if not pais:
        return _WHISPER_HINTS["default"]
    return _WHISPER_HINTS.get(pais.lower().strip(), _WHISPER_HINTS["default"])


def run(
    row_indices: list[int] | None = None,
    pais: str | None = None,
) -> dict[str, int]:
    """
    Transcribe las filas pendientes (sin transcripción, con link de audio).
    row_indices: si se especifica, solo procesa esas filas (1-indexed con header).
    pais: nombre del país en español minúsculas (ej: "argentina"). None = genérico.
    Devuelve {"procesadas": N, "errores": M}.
    """
    creds = get_google_credentials()
    sheets = SheetsClient(creds)
    openai_client = openai.OpenAI(api_key=get_secret("OPENAI_API_KEY"))
    drive_token = _get_drive_token(creds)

    hint = _whisper_hint(pais)
    logger.info("Whisper hint: %s", hint)

    respuestas = sheets.get_respuestas()
    pendientes = [
        r for r in respuestas
        if r["link_audio"].strip()
        and not r["transcripcion"].strip()
        and (row_indices is None or r["row_index"] in row_indices)
    ]

    if not pendientes:
        logger.info("No hay filas pendientes de transcripción.")
        return {"procesadas": 0, "errores": 0}

    logger.info("%d filas a transcribir.", len(pendientes))
    procesadas = errores = 0

    for row in pendientes:
        nombre = row["nombre"]
        pregunta = row["pregunta"]
        link = row["link_audio"]

        logger.info("Transcribiendo: %s — Pregunta %s", nombre, pregunta)

        try:
            file_id = _extract_drive_id(link)
            audio_bytes = _download_drive_file(file_id, drive_token)
            filename = _guess_filename(link, nombre, pregunta)
            transcripcion = _transcribir(openai_client, audio_bytes, filename, hint)
            sheets.write_transcripcion(row["row_index"], transcripcion)
            logger.info("OK: %s P%s — %d chars", nombre, pregunta, len(transcripcion))
            procesadas += 1
        except Exception as e:
            logger.error("Error en %s P%s: %s", nombre, pregunta, e)
            errores += 1

    return {"procesadas": procesadas, "errores": errores}


def _extract_drive_id(url: str) -> str:
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "id" in qs:
        return qs["id"][0]
    raise ValueError(f"No se pudo extraer Drive ID de: {url}")


def _get_drive_token(credentials) -> str:
    import google.auth.transport.requests
    req = google.auth.transport.requests.Request()
    credentials.refresh(req)
    return credentials.token


def _download_drive_file(file_id: str, token: str) -> bytes:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, headers=headers, timeout=120)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 401 and attempt < MAX_RETRIES - 1:
            logger.warning("Token expirado, reintentando...")
            time.sleep(RETRY_DELAY)
            continue
        resp.raise_for_status()

    raise RuntimeError(f"No se pudo descargar el archivo {file_id}")


def _guess_filename(link: str, nombre: str, pregunta: str) -> str:
    for ext in ["webm", "ogg", "mp4", "m4a", "mp3", "wav"]:
        if ext in link.lower():
            return f"{nombre}_P{pregunta}.{ext}"
    return f"{nombre}_P{pregunta}.webm"


def _transcribir(
    client: openai.OpenAI,
    audio_bytes: bytes,
    filename: str,
    hint: str,
) -> str:
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    for attempt in range(MAX_RETRIES):
        try:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                language=WHISPER_LANGUAGE,
                prompt=hint,
                response_format="text",
            )
            return response.strip()
        except openai.RateLimitError:
            wait = RETRY_DELAY * (2 ** attempt)
            logger.warning("Rate limit, esperando %ds...", wait)
            time.sleep(wait)
            audio_file.seek(0)
        except Exception:
            raise

    raise RuntimeError(f"Whisper falló tras {MAX_RETRIES} intentos.")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    # Uso: python transcriber.py [pais] [row_index ...]
    # Ej:  python transcriber.py argentina
    #      python transcriber.py chile 3 4 5
    args = sys.argv[1:]
    pais_arg = None
    indices_arg = None
    if args:
        paises_conocidos = set(_WHISPER_HINTS.keys()) - {"default"}
        if args[0].lower() in paises_conocidos:
            pais_arg = args[0]
            indices_arg = [int(x) for x in args[1:]] or None
        else:
            indices_arg = [int(x) for x in args] or None
    resultado = run(row_indices=indices_arg, pais=pais_arg)
    print(resultado)
