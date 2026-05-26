#!/usr/bin/env python3
"""
Reads Firestore for respuestas where `transcripcion` is null or empty, downloads
each audio from `audio_url` (gs:// path) using the Service Account / ADC credentials,
transcribes with Whisper API (whisper-1, language=es), and writes the result
back to Firestore in the `transcripcion` field.

Idempotent: documents that already have a non-empty `transcripcion` are skipped.
Parallel: up to MAX_WORKERS simultaneous Whisper requests.
"""
import io
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import firestore, storage
from google.oauth2 import service_account
from openai import OpenAI

PROJECT_ID = "familia-marino"
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
    """
    Walk all familias → integrantes → respuestas and return items where
    `transcripcion` is null or empty and `audio_url` is a valid gs:// path.
    """
    pending = []
    for familia_doc in db.collection("familias").stream():
        familia_id = familia_doc.id
        familia_nombre = familia_doc.to_dict().get("nombre", familia_id)

        for integrante_doc in (
            db.collection("familias").document(familia_id)
            .collection("integrantes").stream()
        ):
            integrante_id = integrante_doc.id
            nombre = integrante_doc.to_dict().get("nombre", integrante_id)

            for resp_doc in (
                db.collection("familias").document(familia_id)
                .collection("integrantes").document(integrante_id)
                .collection("respuestas").stream()
            ):
                data = resp_doc.to_dict()

                # Skip if transcripcion already exists
                if data.get("transcripcion", "").strip():
                    continue

                audio_url = data.get("audio_url", "").strip()
                if not audio_url:
                    log.warning(
                        f"SKIP {familia_id}/{integrante_id}/{resp_doc.id}: "
                        "audio_url is missing"
                    )
                    continue
                if not audio_url.startswith("gs://"):
                    log.warning(
                        f"SKIP {familia_id}/{integrante_id}/{resp_doc.id}: "
                        f"audio_url is not a GCS path: {audio_url}"
                    )
                    continue

                pending.append({
                    "familia_id": familia_id,
                    "familia_nombre": familia_nombre,
                    "integrante_id": integrante_id,
                    "nombre": nombre,
                    "pregunta_id": resp_doc.id,
                    "audio_url": audio_url,
                    "ref": resp_doc.reference,
                })

    return pending


def _transcribe_one(item: dict, gcs: storage.Client, openai_client: OpenAI) -> dict:
    """Download audio from GCS and transcribe with Whisper."""
    label = f"[{item['familia_nombre']} / {item['nombre']}] {item['pregunta_id']}"
    try:
        bucket_name, blob_name = _parse_gcs_path(item["audio_url"])
        audio_bytes = gcs.bucket(bucket_name).blob(blob_name).download_as_bytes()

        # Blob names have no extension (e.g. marino-saraniti/integrante/1).
        # Supply .webm so Whisper can detect the format.
        ext = blob_name.rsplit(".", 1)[-1] if "." in blob_name.split("/")[-1] else "webm"
        filename = f"{item['pregunta_id']}.{ext}"

        response = openai_client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(filename, io.BytesIO(audio_bytes)),
            language="es",
        )
        transcripcion = response.text.strip()
        log.info(f"OK {label}: {transcripcion[:80]}…")
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
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is required.\n"
            "Example:\n"
            "  OPENAI_API_KEY=OGVHLDdf8Rt0ig2AYAgemMK-1kS2SPCgqLMQyQ1xPj8KFwRz7Y-dAFb8ezYtUWlf_-QtIBXOlhT3BlbkFJlkYj41XFVlDZBUEkvyzQqZDC9nUkaSoIPnpc8oxzYQ0qQ1qn0tdYYRF3t6mKjVetEnsLGSkLMA "
            "python pipeline/utils/transcribe_pending.py"
        )
    openai_client = OpenAI(api_key=api_key)

    log.info("Collecting pending transcriptions from Firestore...")
    pending = _collect_pending(db)
    log.info(f"Found {len(pending)} audios pending transcription")

    if not pending:
        print("Nada para transcribir.")
        return

    # Results grouped by familia for the final report
    by_familia: dict[str, dict] = defaultdict(lambda: {"ok": [], "errors": []})

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_transcribe_one, item, gcs, openai_client): item
            for item in pending
        }
        for future in as_completed(futures):
            result = future.result()
            item = result["item"]
            fid = item["familia_id"]
            fnombre = item["familia_nombre"]
            label = f"{item['nombre']} / {item['pregunta_id']}"

            if result["error"]:
                by_familia[fid]["nombre"] = fnombre
                by_familia[fid]["errors"].append({
                    "label": label,
                    "audio": item["audio_url"],
                    "error": result["error"],
                })
            else:
                item["ref"].update({"transcripcion": result["transcripcion"]})
                log.info(f"SAVED [{fnombre}] {label}")
                by_familia[fid]["nombre"] = fnombre
                by_familia[fid]["ok"].append(label)

    # ── Report grouped by familia ──────────────────────────────────────────────
    total_ok = sum(len(v["ok"]) for v in by_familia.values())
    total_err = sum(len(v["errors"]) for v in by_familia.values())

    print("\n" + "=" * 60)
    print("REPORTE DE TRANSCRIPCIÓN")
    print(f"  Total OK     : {total_ok}")
    print(f"  Total errores: {total_err}")
    print("=" * 60)

    for fid, data in sorted(by_familia.items()):
        fnombre = data.get("nombre", fid)
        ok_count = len(data["ok"])
        err_count = len(data["errors"])
        print(f"\n  📁 {fnombre}  ({ok_count} OK, {err_count} errores)")
        for label in data["ok"]:
            print(f"    ✅ {label}")
        for e in data["errors"]:
            print(f"    ❌ {e['label']}")
            print(f"       audio : {e['audio']}")
            print(f"       error : {e['error']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    transcribe_pending()
