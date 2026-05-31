"""
Transcriber agent — convierte audios a texto con Whisper (OpenAI).

Fuentes soportadas:
  - GCS (gs://bucket/path)  ← sistema actual (Firestore + GCS)
  - Google Drive URL        ← sistema legado (Sheets) — solo como fallback
"""

import os
import tempfile

from openai import OpenAI

# Regional vocabulary hints to nudge Whisper's acoustic model.
_VOCAB_HINTS: dict[str, str] = {
    "argentina": (
        "che, boludo, pibe, mina, laburo, quilombo, morfar, chabón, guita, copado, "
        "birome, colectivo, campera, heladera, boliche, asado, mate, yerba, pileta, "
        "reo, trucho, posta, joda, fiaca, macanudo, bondi, verdulería, kiosco"
    ),
    "uruguay": (
        "che, gurí, pila, ta, barra, farra, cañero, torta, boliche, rambla, "
        "candombe, manya, ta bien, un toque, pinta, cuchita, chiquilín"
    ),
    "chile": (
        "huevón, cachai, po, fome, cuático, bacán, pololo, polola, once, "
        "carrete, pega, plata, micro, guagua, cabro, buena onda, al tiro, piola"
    ),
    "colombia": (
        "parcero, parce, bacano, berraco, chimba, marica, listo, "
        "chévere, qué más, pues, vaina, plata, finca, tinto, aguardiente, rumba"
    ),
    "mexico": (
        "güey, wey, chido, chavo, chava, neta, órale, ándale, chamba, lana, "
        "cuate, chela, torta, chilango, mande, sale, a huevo, chingón"
    ),
    "venezuela": (
        "chamo, chama, pana, coño, vaina, chimbo, broma, ladilla, burda, "
        "catire, hallaca, arepa, cachapa, pabellón, ¿qué fue?, bacán"
    ),
    "peru": (
        "causa, pata, llave, bacán, pe, ah no, está bravazo, "
        "chamba, chibolo, jerma, a la orden, ceviche, lomo saltado"
    ),
    "españa": (
        "tío, tía, mola, guay, chulo, vale, hostia, joder, coño, mazo, "
        "colega, pasta, curro, mogollón, chaval, flipar, rollo, venga"
    ),
}
_DEFAULT_HINTS = (
    "familia, recuerdos, infancia, trabajo, amor, abuelos, hijos, "
    "nietos, historia, vida, pueblo, campo, ciudad"
)


def _get_prompt(pais: str) -> str:
    base = _VOCAB_HINTS.get(pais.lower().strip(), _DEFAULT_HINTS)
    return (
        f"Transcripción en español. Vocabulario regional: {base}. "
        "Incluir muletillas y expresiones coloquiales tal como se dicen."
    )


def _download_audio(audio_uri: str, dest_path: str) -> None:
    """Descarga el audio desde GCS (gs://) o Google Drive (https://)."""
    if audio_uri.startswith("gs://"):
        from google.cloud import storage
        parts = audio_uri[5:].split("/", 1)
        bucket_name, blob_name = parts[0], parts[1]
        storage.Client().bucket(bucket_name).blob(blob_name).download_to_filename(dest_path)
    else:
        # Fallback Drive legado
        from pipeline.utils import sheets as _sheets
        _sheets.download_drive_file(audio_uri, dest_path)


def run_gcs(pais: str = "argentina") -> dict:
    """
    Transcribe todos los audios pendientes desde Firestore + GCS.
    Lee respuestas sin transcripción, baja el audio, llama a Whisper,
    guarda de vuelta en Firestore.
    """
    from pipeline.utils import firestore as fs

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = _get_prompt(pais)

    pendientes = fs.get_respuestas_sin_transcribir()
    print(f"[transcriber] {len(pendientes)} audio(s) sin transcripción encontrados")

    procesadas = 0
    errores = 0
    detalles: list[dict] = []

    for resp in pendientes:
        doc_id   = resp["_id"]
        nombre   = resp.get("nombre", "?")
        pregunta = resp.get("pregunta", resp.get("pregunta_num", "?"))
        uri      = resp["audio_uri"]

        # Determinar extensión por el URI
        ext = ".webm"
        for candidate in (".mp3", ".ogg", ".wav", ".m4a", ".mp4", ".webm"):
            if uri.lower().endswith(candidate):
                ext = candidate
                break

        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_path = tmp.name

            try:
                _download_audio(uri, tmp_path)

                with open(tmp_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="es",
                        prompt=prompt,
                    )

                transcripcion = result.text.strip()
                fs.save_transcripcion(doc_id, transcripcion)
                procesadas += 1
                print(f"  ✓ {nombre} / pregunta {pregunta} ({len(transcripcion)} chars)")
                detalles.append({"nombre": nombre, "pregunta": pregunta, "chars": len(transcripcion), "ok": True})

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            print(f"  ✗ {nombre} / pregunta {pregunta}: {e}")
            errores += 1
            detalles.append({"nombre": nombre, "pregunta": pregunta, "error": str(e), "ok": False})

    return {"procesadas": procesadas, "errores": errores, "total_pendientes": len(pendientes), "detalles": detalles}


# ── Función legacy (Sheets + Drive) — se mantiene para compatibilidad ─────────

def run(row_indices: list[int], pais: str = "argentina") -> dict:
    """
    Transcribe audios por índice de fila del Sheet (sistema legado).
    Se mantiene para que orchestrator.py siga funcionando sin cambios.
    """
    from pipeline.utils import sheets

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = _get_prompt(pais)

    all_rows = sheets.get_all_rows()
    procesadas = 0
    errores = 0

    for row_idx in row_indices:
        try:
            row = all_rows[row_idx - 1]
            audio_url = row[sheets.COL_LINK_AUDIO - 1].strip() if len(row) >= sheets.COL_LINK_AUDIO else ""

            if not audio_url:
                errores += 1
                continue

            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                _download_audio(audio_url, tmp_path)

                with open(tmp_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="es",
                        prompt=prompt,
                    )

                transcripcion = result.text.strip()
                sheets.save_transcription(row_idx, transcripcion)
                procesadas += 1

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            print(f"[transcriber] Error en fila {row_idx}: {e}")
            errores += 1

    return {"procesadas": procesadas, "errores": errores}
