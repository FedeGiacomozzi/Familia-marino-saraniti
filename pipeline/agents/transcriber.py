"""
Whisper-based transcriber agent.
Reads audio links from the Sheet, transcribes each one, writes back to col F.
"""

import os
import tempfile
import time

from openai import OpenAI

from pipeline.utils import sheets

# Regional vocabulary hints to nudge Whisper's acoustic model.
# These are NOT for analysis — voice_agent handles linguistic profiling.
_VOCAB_HINTS: dict[str, str] = {
    "argentina": (
        "che, boludo, pibe, mina, laburo, quilombo, morfar, chabón, guita, copado, "
        "birome, colectivo, campera, heladera, boliche, asado, mate, yerba, pileta, "
        "reo, trucho, posta, joda, fiaca, macanudo, bondi, verdulería, kiosco"
    ),
    "uruguay": (
        "che, gurí, pila, ta, barra, farra, cañero, torta, boliche, rambla, "
        "candombe, manya, manya eso, ta bien, un toque, pinta, cuchita, chiquilín"
    ),
    "chile": (
        "huevón, cachai, po, fome, cuático, bacán, pololo, polola, once, "
        "carrete, pega, plata, micro, guagua, cabro, buena onda, al tiro, "
        "agarrar papa, piola, weon, nan"
    ),
    "colombia": (
        "parcero, parce, bacano, berraco, chimba, gonorrea, marica, listo, "
        "chévere, qué más, pues, vaina, plata, finca, tinto, aguardiente, "
        "rumba, jarta, estar mamado"
    ),
    "mexico": (
        "güey, wey, chido, chavo, chava, neta, órale, ándale, chamba, lana, "
        "cuate, chela, torta, taco, chilango, mande, sale, a huevo, chingón, "
        "pendejo, mamón, naco, fresa"
    ),
    "venezuela": (
        "chamo, chama, pana, coño, vaina, chimbo, broma, ladilla, burda, "
        "arrechera, vergación, catire, hallaca, arepa, cachapa, pabellón, "
        "¿qué fue?, bacán, estar arecho"
    ),
    "peru": (
        "causa, pata, llave, bacán, pata, pe, ah no, está bravazo, "
        "chamba, chibolo, jerma, a la orden, seco y volteado, "
        "ceviche, lomo saltado, ¿cómo así?"
    ),
    "españa": (
        "tío, tía, mola, guay, chulo, vale, hostia, joder, coño, mazo, "
        "pisha, colega, pasta, curro, mogollón, chaval, flipar, rollo, "
        "¿qué tal?, venga"
    ),
}
_DEFAULT_HINTS = (
    "familia, recuerdos, infancia, trabajo, amor, abuelos, hijos, "
    "nietos, historia, vida, pueblo, campo, ciudad"
)


def _get_prompt(pais: str) -> str:
    key = pais.lower().strip()
    base = _VOCAB_HINTS.get(key, _DEFAULT_HINTS)
    return (
        f"Transcripción en español rioplatense. Vocabulario regional: {base}. "
        "Incluir muletillas y expresiones coloquiales tal como se dicen."
    )


_MAX_INTENTOS = 3
_RETRY_DELAYS = [5, 15, 30]  # segundos entre intentos


def _transcribir_con_retry(client: OpenAI, tmp_path: str, prompt: str, row_idx: int, nombre: str, pregunta: str) -> str:
    last_err = None
    for intento in range(1, _MAX_INTENTOS + 1):
        try:
            with open(tmp_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="es",
                    prompt=prompt,
                )
            return result.text.strip()
        except Exception as e:
            last_err = e
            if intento < _MAX_INTENTOS:
                delay = _RETRY_DELAYS[intento - 1]
                print(f"[transcriber] Intento {intento}/{_MAX_INTENTOS} falló para fila {row_idx} ({nombre} / pregunta {pregunta}): {e}. Reintentando en {delay}s...")
                time.sleep(delay)
            else:
                print(f"[transcriber] Todos los intentos fallaron para fila {row_idx} ({nombre} / pregunta {pregunta}): {e}")
    raise last_err


def run(row_indices: list[int], pais: str = "argentina", solo_nuevas: bool = False) -> dict:
    """
    Transcribe audio for the given sheet row indices (1-based, skipping header).
    Updates col F (Transcripción) in the Sheet for each row.
    solo_nuevas=True skips rows that already have a transcription.
    Returns {"procesadas": N, "omitidas": K, "errores": M, "detalle_errores": [...]}.
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = _get_prompt(pais)

    all_rows = sheets.get_all_rows()
    procesadas = 0
    omitidas = 0
    errores = 0
    detalle_errores = []

    for row_idx in row_indices:
        try:
            # row_idx is 1-based; all_rows is 0-based
            row = all_rows[row_idx - 1]
            pregunta = row[sheets.COL_PREGUNTA - 1] if len(row) >= sheets.COL_PREGUNTA else f"fila {row_idx}"
            nombre = row[sheets.COL_NOMBRE - 1] if len(row) >= sheets.COL_NOMBRE else "?"

            # Skip if already transcribed and solo_nuevas is set
            if solo_nuevas:
                existing = row[sheets.COL_TRANSCRIPCION - 1].strip() if len(row) >= sheets.COL_TRANSCRIPCION else ""
                if existing:
                    omitidas += 1
                    print(f"[transcriber] Omitiendo fila {row_idx} ({nombre} / pregunta {pregunta}): ya tiene transcripción")
                    continue

            audio_url = row[sheets.COL_LINK_AUDIO - 1].strip() if len(row) >= sheets.COL_LINK_AUDIO else ""

            if not audio_url:
                msg = f"fila {row_idx} ({nombre} / pregunta {pregunta}): sin link de audio en columna E"
                print(f"[transcriber] Saltando {msg}")
                detalle_errores.append(msg)
                errores += 1
                continue

            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                print(f"[transcriber] Descargando fila {row_idx} ({nombre} / pregunta {pregunta})...")
                sheets.download_drive_file(audio_url, tmp_path)

                transcripcion = _transcribir_con_retry(client, tmp_path, prompt, row_idx, nombre, pregunta)
                sheets.save_transcription(row_idx, transcripcion)
                procesadas += 1
                print(f"[transcriber] OK fila {row_idx} ({nombre} / pregunta {pregunta}): {len(transcripcion)} chars")

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            msg = f"fila {row_idx} ({nombre} / pregunta {pregunta}): {e}"
            print(f"[transcriber] Error en {msg}")
            detalle_errores.append(msg)
            errores += 1

    return {"procesadas": procesadas, "omitidas": omitidas, "errores": errores, "detalle_errores": detalle_errores}
