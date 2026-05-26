#!/usr/bin/env python3
"""
Reads Firestore for respuestas with empty transcripcion, downloads each audio
from GCS, sends it to Whisper API (whisper-1, es), and writes the result back
to Firestore. Processes up to 3 audios in parallel.
"""
import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import firestore, storage
from google.oauth2 import service_account
from openai import OpenAI

PROJECT_ID = "familia-marino"
BUCKET_AUDIOS = "libro-familiar-audios"
WHISPER_MODEL = "whisper-1"
MAX_WORKERS = 3

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ─── Auth ─────────────────────────────────────────────────────────────────────


def _get_creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE")
    if creds_file:
        with open(creds_file) as f:
            info = json.load(f)
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    import google.auth
    creds, _ = google.auth.default(scopes=_SCOPES)
    return creds


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    path = gcs_path.removeprefix("gs://")
    bucket_name, *parts = path.split("/")
    return bucket_name, "/".join(parts)


def _collect_pending(db: firestore.Client) -> list[dict]:
    """Return all respuestas docs where transcripcion is empty."""
    pending = []
    familias = db.collection("familias").stream()
    for familia_doc in familias:
        familia_id = familia_doc.id
        integrantes = (
            db.collection("familias").document(familia_id).collection("integrantes").stream()
        )
        for integrante_doc in integrantes:
            integrante_id = integrante_doc.id
            nombre = integrante_doc.to_dict().get("nombre", integrante_id)
            respuestas = (
                db.collection("familias")
                .document(familia_id)
                .collection("integrantes")
                .document(integrante_id)
                .collection("respuestas")
                .stream()
            )
            for resp_doc in respuestas:
                data = resp_doc.to_dict()
                if not data.get("transcripcion", "").strip():
                    audio_url = data.get("audio_url", "")
                    if audio_url.startswith("gs://"):
                        pending.append({
                            "familia_id": familia_id,
                            "integrante_id": integrante_id,
                            "nombre": nombre,
                            "pregunta_id": resp_doc.id,
                            "audio_url": audio_url,
                            "ref": resp_doc.reference,
                        })
                    else:
                        log.warning(
                            f"SKIP {familia_id}/{integrante_id}/{resp_doc.id}: "
                            f"audio_url is not a GCS path: {audio_url}"
                        )
    return pending


def _transcribe_one(item: dict, gcs: storage.Client, openai: OpenAI) -> dict:
    """Download audio from GCS and transcribe with Whisper. Returns result dict."""
    label = f"[{item['nombre']}] {item['pregunta_id']}"
    try:
        bucket_name, blob_name = _parse_gcs_path(item["audio_url"])
        audio_bytes = gcs.bucket(bucket_name).blob(blob_name).download_as_bytes()
        # Whisper needs a filename with extension to detect format
        ext = blob_name.split(".")[-1] if "." in blob_name else "webm"
        filename = f"{item['pregunta_id']}.{ext}"

        response = openai.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(filename, io.BytesIO(audio_bytes)),
            language="es",
        )
        transcripcion = response.text.strip()
        log.info(f"OK {label}: {transcripcion[:60]}…")
        return {"item": item, "transcripcion": transcripcion, "error": None}
    except Exception as exc:
        log.error(f"ERROR {label}: {exc}")
        return {"item": item, "transcripcion": None, "error": str(exc)}


# ─── Main ─────────────────────────────────────────────────────────────────────


def transcribe_pending():
    creds = _get_creds()
    db = firestore.Client(project=PROJECT_ID, credentials=creds)
    gcs = storage.Client(project=PROJECT_ID, credentials=creds)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required")
    openai = OpenAI(api_key=api_key)

    log.info("Collecting pending transcriptions from Firestore...")
    pending = _collect_pending(db)
    log.info(f"Found {len(pending)} audios pending transcription")

    if not pending:
        print("Nothing to transcribe.")
        return

    stats = {"ok": 0, "errors": 0, "skipped": 0}
    errores = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_transcribe_one, item, gcs, openai): item
            for item in pending
        }
        for future in as_completed(futures):
            result = future.result()
            item = result["item"]
            label = f"[{item['nombre']}] {item['pregunta_id']}"
            if result["error"]:
                stats["errors"] += 1
                errores.append({"audio": item["audio_url"], "error": result["error"]})
            else:
                # Write transcription back to Firestore
                item["ref"].update({"transcripcion": result["transcripcion"]})
                log.info(f"SAVED {label}")
                stats["ok"] += 1

    print("\n" + "=" * 60)
    print("REPORTE DE TRANSCRIPCIÓN")
    print(f"  Transcriptos OK : {stats['ok']}")
    print(f"  Errores         : {stats['errors']}")
    if errores:
        print("\n  Detalle errores:")
        for e in errores:
            print(f"    {e['audio']}")
            print(f"    → {e['error']}")
    print("=" * 60)


if __name__ == "__main__":
    transcribe_pending()
