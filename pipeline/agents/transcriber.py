"""
Whisper-based transcriber agent.
Lee audios desde GCS (via Firestore doc ids o lista de docs sin transcripción),
transcribe cada uno con Whisper y guarda la transcripción en Firestore.
"""

import os
import tempfile

from openai import OpenAI

from pipeline.utils import firestore as db
from pipeline.utils import storage

# Regional vocabulary hints to nudge Whisper's acoustic model.
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


def run(
    doc_ids: list[str] | None = None,
    pais: str = "argentina",
    nombre: str | None = None,
    solo_pendientes: bool = True,
) -> dict:
    """
    Transcribe audios desde GCS y guarda en Firestore.

    Args:
        doc_ids: IDs de documentos Firestore a procesar. Si None, procesa
                 todos los pendientes (sin transcripción) del nombre dado.
        pais: código de país para hints de vocabulario Whisper.
        nombre: filtro opcional por integrante.
        solo_pendientes: si True (default) omite docs que ya tienen transcripción.

    Returns:
        {"procesadas": N, "errores": M}
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = _get_prompt(pais)

    if doc_ids:
        # Cargar docs por ID explícito
        all_docs = db.get_all_respuestas()
        docs = [d for d in all_docs if d["_id"] in doc_ids]
        if solo_pendientes:
            docs = [d for d in docs if not d.get("transcripcion", "").strip()]
    else:
        docs = db.get_respuestas_sin_transcripcion(nombre)

    procesadas = 0
    errores = 0

    for doc in docs:
        doc_id = doc["_id"]
        audio_url = doc.get("link_audio", "").strip()
        if not audio_url:
            errores += 1
            continue

        ext = _audio_ext(audio_url)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            storage.download_audio(audio_url, tmp_path)

            with open(tmp_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="es",
                    prompt=prompt,
                )

            transcripcion = result.text.strip()
            db.save_transcripcion(doc_id, transcripcion)
            print(f"[transcriber] ✓ {doc.get('nombre', '?')} / {doc.get('pregunta', '?')}")
            procesadas += 1

        except Exception as e:
            print(f"[transcriber] Error en doc {doc_id}: {e}")
            errores += 1

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return {"procesadas": procesadas, "errores": errores}


def _audio_ext(url: str) -> str:
    for ext in (".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm", ".flac"):
        if url.lower().endswith(ext):
            return ext
    return ".webm"
