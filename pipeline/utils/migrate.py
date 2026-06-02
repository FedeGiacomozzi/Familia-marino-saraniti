#!/usr/bin/env python3
"""
One-shot migration: Drive + Sheets → GCS + Firestore.
Idempotent via GCS-first: blob.exists() is checked before any Drive call.
Drive is only touched when a blob is actually missing from GCS.
DO NOT run without coordinator confirmation. Never modifies Drive or Sheets.

Outputs at the end:
  - migration_report.json
  - Firestore: reportes/migracion
"""
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import gspread
from google.cloud import firestore, storage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ─── Constants ────────────────────────────────────────────────────────────────

SHEET_ID = "1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM"
FOLDER_ID = "1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3"
PROJECT_ID = "familia-marino"
FAMILIA_ID = "marino-saraniti"
FAMILIA_NOMBRE = "Mariño-Saraniti"

BUCKET_AUDIOS = "libro-familiar-audios"
BUCKET_FOTOS = "libro-familiar-fotos"
BUCKET_LIBROS = "libro-familiar-libros"

LOG_FILE = "migrate.log"
REPORT_FILE = "migration_report.json"

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/cloud-platform",
]

_AUDIO_MIMES = {
    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav",
    "audio/webm", "audio/x-m4a", "video/mp4",
}
_FOTO_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
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
    # Fallback: Application Default Credentials (Cloud Shell, Cloud Run, GCE)
    import google.auth
    creds, _ = google.auth.default(scopes=_SCOPES)
    return creds


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _extract_file_id(url: str) -> str | None:
    for pattern in [r"/d/([a-zA-Z0-9_-]+)", r"id=([a-zA-Z0-9_-]+)"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _download_bytes(drive_service, file_id: str) -> bytes:
    request = drive_service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _gcs_upload(gcs: storage.Client, bucket_name: str, blob_name: str, data: bytes, content_type: str) -> str:
    gcs.bucket(bucket_name).blob(blob_name).upload_from_string(data, content_type=content_type)
    url = f"gs://{bucket_name}/{blob_name}"
    log.info(f"UPLOADED {url}")
    return url


def _find_existing_foto(gcs: storage.Client, integrante_id: str) -> str | None:
    """Return gs:// URL if any foto.* blob exists for this integrante, else None."""
    prefix = f"{FAMILIA_ID}/{integrante_id}/foto."
    blobs = list(gcs.bucket(BUCKET_FOTOS).list_blobs(prefix=prefix, max_results=1))
    if blobs:
        url = f"gs://{BUCKET_FOTOS}/{blobs[0].name}"
        log.info(f"SKIP (exists) {url}")
        return url
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Report ───────────────────────────────────────────────────────────────────


def _build_report(integrantes: dict) -> dict:
    con_transcripcion = []
    sin_transcripcion = []

    for integrante_id, data in integrantes.items():
        nombre = data["nombre"]
        for resp in data["respuestas"]:
            audio_url = resp.get("audio_gcs_url") or resp.get("audio_url_drive", "")
            transcripcion = resp.get("transcripcion", "").strip()
            entry = {
                "familia": FAMILIA_NOMBRE,
                "familia_id": FAMILIA_ID,
                "integrante": nombre,
                "integrante_id": integrante_id,
                "pregunta_id": resp["pregunta_id"],
                "audio_url": audio_url,
            }
            if transcripcion:
                entry["transcripcion_preview"] = transcripcion[:120] + ("…" if len(transcripcion) > 120 else "")
                con_transcripcion.append(entry)
            else:
                entry["pendiente_whisper"] = True
                sin_transcripcion.append(entry)

    return {
        "familia": FAMILIA_NOMBRE,
        "familia_id": FAMILIA_ID,
        "fecha_reporte": _now_iso(),
        "con_transcripcion": con_transcripcion,
        "sin_transcripcion": sin_transcripcion,
        "resumen": {
            "total_integrantes": len(integrantes),
            "total_audios": len(con_transcripcion) + len(sin_transcripcion),
            "con_transcripcion": len(con_transcripcion),
            "sin_transcripcion": len(sin_transcripcion),
        },
    }


def _print_report(report: dict):
    r = report["resumen"]
    print("\n" + "=" * 60)
    print("REPORTE DE AUDIOS MIGRADOS")
    print(f"  Familia            : {report['familia']}")
    print(f"  Integrantes        : {r['total_integrantes']}")
    print(f"  Total audios       : {r['total_audios']}")
    print(f"  Con transcripción  : {r['con_transcripcion']}")
    print(f"  Sin transcripción  : {r['sin_transcripcion']}  ← pendientes Whisper")
    print("=" * 60)

    if report["con_transcripcion"]:
        print("\n✅ CON TRANSCRIPCIÓN:")
        for e in report["con_transcripcion"]:
            print(f"  [{e['integrante']}] {e['pregunta_id']}")
            print(f"    audio : {e['audio_url']}")
            print(f"    texto : {e.get('transcripcion_preview', '')}")

    if report["sin_transcripcion"]:
        print("\n⚠️  SIN TRANSCRIPCIÓN (pendientes de Whisper):")
        for e in report["sin_transcripcion"]:
            print(f"  [{e['integrante']}] {e['pregunta_id']}")
            print(f"    audio : {e['audio_url']}")

    print("=" * 60)


# ─── Migration ────────────────────────────────────────────────────────────────


def migrate():
    creds = _get_creds()
    gcs = storage.Client(project=PROJECT_ID, credentials=creds)
    db = firestore.Client(project=PROJECT_ID, credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gc = gspread.authorize(creds)

    stats = {"archivos_migrados": 0, "registros_creados": 0, "errores": 0, "omitidos": 0}

    # ── 1. Read Respuestas sheet ───────────────────────────────────────────────
    log.info("Reading sheet Respuestas...")
    ws = gc.open_by_key(SHEET_ID).worksheet("Respuestas")
    rows = ws.get_all_values()

    integrantes: dict[str, dict] = {}
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        nombre = row[1].strip() if len(row) > 1 else ""
        if not nombre:
            continue
        integrante_id = _slug(nombre)
        fecha_nac = row[2].strip() if len(row) > 2 else ""
        pregunta = row[3].strip() if len(row) > 3 else ""
        audio_url = row[4].strip() if len(row) > 4 else ""
        transcripcion = row[5].strip() if len(row) > 5 else ""
        foto_url = row[6].strip() if len(row) > 6 else ""

        if integrante_id not in integrantes:
            integrantes[integrante_id] = {
                "nombre": nombre,
                "fecha_nac": fecha_nac,
                "foto_url_drive": foto_url,
                "respuestas": [],
            }
        if pregunta or audio_url:
            idx = len(integrantes[integrante_id]["respuestas"]) + 1
            integrantes[integrante_id]["respuestas"].append({
                "pregunta_id": _slug(pregunta) if pregunta else f"p{idx}",
                "audio_url_drive": audio_url,
                "audio_gcs_url": None,
                "transcripcion": transcripcion,
            })

    log.info(f"Found {len(integrantes)} integrantes in sheet")

    # ── 2. Upsert familia document ─────────────────────────────────────────────
    familia_ref = db.collection("familias").document(FAMILIA_ID)
    familia_ref.set(
        {
            "nombre": FAMILIA_NOMBRE,
            "estado": "entregado",
            "pack": "completo",
            "fecha_migracion": _now_iso(),
        },
        merge=True,
    )
    log.info(f"Familia '{FAMILIA_ID}' upserted in Firestore")
    stats["registros_creados"] += 1

    # ── 3. Per-integrante: migrate foto + audios + Firestore docs ─────────────
    for integrante_id, data in integrantes.items():
        nombre = data["nombre"]
        log.info(f"Processing integrante: {nombre}")

        # Foto — check GCS first, Drive only if missing
        foto_gcs_url = _find_existing_foto(gcs, integrante_id)
        if foto_gcs_url:
            stats["omitidos"] += 1
        else:
            foto_drive_url = data.get("foto_url_drive", "")
            file_id = _extract_file_id(foto_drive_url) if foto_drive_url else None
            if file_id:
                try:
                    meta = drive.files().get(fileId=file_id, fields="mimeType").execute()
                    mime = meta.get("mimeType", "image/jpeg")
                    ext = mime.split("/")[-1] if "/" in mime else "jpg"
                    blob_name = f"{FAMILIA_ID}/{integrante_id}/foto.{ext}"
                    foto_bytes = _download_bytes(drive, file_id)
                    foto_gcs_url = _gcs_upload(gcs, BUCKET_FOTOS, blob_name, foto_bytes, mime)
                    stats["archivos_migrados"] += 1
                except Exception as exc:
                    log.error(f"ERROR foto {nombre}: {exc}")
                    stats["errores"] += 1

        integrante_ref = familia_ref.collection("integrantes").document(integrante_id)
        integrante_ref.set(
            {
                "nombre": nombre,
                "fecha_nac": data.get("fecha_nac", ""),
                "token_unico": str(uuid.uuid5(uuid.NAMESPACE_DNS, integrante_id)),
                "estado": "completado",
                "foto_url": foto_gcs_url or data.get("foto_url_drive", ""),
                "porcentaje_avance": 100,
                "es_comprador": False,
            },
            merge=True,
        )
        stats["registros_creados"] += 1

        for resp in data["respuestas"]:
            pregunta_id = resp["pregunta_id"]
            audio_drive_url = resp.get("audio_url_drive", "")
            blob_name = f"{FAMILIA_ID}/{integrante_id}/{pregunta_id}"

            # Audio — check GCS first; Drive only if blob is missing
            blob = gcs.bucket(BUCKET_AUDIOS).blob(blob_name)
            if blob.exists():
                log.info(f"SKIP (exists) gs://{BUCKET_AUDIOS}/{blob_name}")
                resp["audio_gcs_url"] = f"gs://{BUCKET_AUDIOS}/{blob_name}"
                stats["omitidos"] += 1
            else:
                file_id = _extract_file_id(audio_drive_url) if audio_drive_url else None
                if file_id:
                    try:
                        meta = drive.files().get(fileId=file_id, fields="mimeType").execute()
                        mime = meta.get("mimeType", "audio/webm")
                        audio_bytes = _download_bytes(drive, file_id)
                        resp["audio_gcs_url"] = _gcs_upload(gcs, BUCKET_AUDIOS, blob_name, audio_bytes, mime)
                        stats["archivos_migrados"] += 1
                    except Exception as exc:
                        log.error(f"ERROR audio {pregunta_id} / {nombre}: {exc}")
                        stats["errores"] += 1

            integrante_ref.collection("respuestas").document(pregunta_id).set(
                {
                    "audio_url": resp.get("audio_gcs_url") or audio_drive_url,
                    "transcripcion": resp.get("transcripcion", ""),
                    "timestamp": _now_iso(),
                },
                merge=True,
            )
            stats["registros_creados"] += 1

    # ── 4. Sweep Drive folder for files not linked from the sheet ─────────────
    log.info("Scanning Drive folder for additional files...")
    page_token = None
    while True:
        kwargs: dict = {
            "q": f"'{FOLDER_ID}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType)",
            "pageSize": 100,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        result = drive.files().list(**kwargs).execute()

        for f in result.get("files", []):
            mime, name, file_id = f["mimeType"], f["name"], f["id"]
            if mime in _AUDIO_MIMES:
                bucket = BUCKET_AUDIOS
            elif mime in _FOTO_MIMES:
                bucket = BUCKET_FOTOS
            elif mime == "application/pdf":
                bucket = BUCKET_LIBROS
            else:
                continue

            blob_name = f"{FAMILIA_ID}/drive/{file_id}/{name}"

            # GCS-first: skip Drive download entirely if blob already exists
            if gcs.bucket(bucket).blob(blob_name).exists():
                log.info(f"SKIP (exists) gs://{bucket}/{blob_name}")
                stats["omitidos"] += 1
                continue

            try:
                raw = _download_bytes(drive, file_id)
                _gcs_upload(gcs, bucket, blob_name, raw, mime)
                stats["archivos_migrados"] += 1
            except Exception as exc:
                log.error(f"ERROR Drive file {name}: {exc}")
                stats["errores"] += 1

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    # ── 5. Build + save report ─────────────────────────────────────────────────
    log.info("Generating audio transcription report...")
    report = _build_report(integrantes)
    _print_report(report)

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info(f"Report saved to {REPORT_FILE}")

    db.collection("reportes").document("migracion").set(report)
    log.info("Report saved to Firestore: reportes/migracion")

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = (
        f"\n{'='*60}\n"
        f"RESUMEN DE MIGRACIÓN\n"
        f"  Archivos migrados  : {stats['archivos_migrados']}\n"
        f"  Registros creados  : {stats['registros_creados']}\n"
        f"  Omitidos (ya exist): {stats['omitidos']}\n"
        f"  Errores            : {stats['errores']}\n"
        f"{'='*60}"
    )
    log.info(summary)
    print(summary)


if __name__ == "__main__":
    migrate()
