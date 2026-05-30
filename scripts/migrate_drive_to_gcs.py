#!/usr/bin/env python3
"""
migrate_drive_to_gcs.py — Migra audios de Google Drive a GCS.

Lee la columna link_audio del Sheet "Respuestas", descarga cada archivo de Drive
y lo sube al bucket libro-familiar-audios en GCS.
NO borra nada de Drive.

Convención de nombre en GCS:
  {nombre_key}/{pregunta_key}/{nombre_original_del_archivo}
  Ej: maria_lopez/pregunta_01/audio.webm

Al terminar, actualiza el campo link_audio en la respuesta de Firestore
(si seed_firestore ya fue ejecutado) con la URL gs://.

Usage:
  GCP_SA_KEY_JSON="$(cat key.json)" python scripts/migrate_drive_to_gcs.py [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import tempfile

AUDIO_BUCKET = os.environ.get("AUDIO_BUCKET", "libro-familiar-audios")
SHEET_ID = "1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM"
PROJECT = os.environ.get("GCP_PROJECT_ID", "familia-marino")
FAMILIA_ID = os.environ.get("FAMILIA_ID", "marino-saraniti")

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]
GCS_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]
FIRESTORE_SCOPES = ["https://www.googleapis.com/auth/datastore"]


def _creds(scopes: list[str]):
    from google.oauth2 import service_account
    raw = os.environ.get("GCP_SA_KEY_JSON", "")
    if not raw:
        raise SystemExit("Falta GCP_SA_KEY_JSON")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        with open(raw) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def _extract_drive_id(url: str) -> str | None:
    for pattern in [r"/d/([a-zA-Z0-9_-]+)", r"id=([a-zA-Z0-9_-]+)"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _nombre_key(nombre: str) -> str:
    return nombre.strip().lower().replace(" ", "_")


def _safe_key(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", s.strip())[:40]


def read_sheet_rows() -> list[dict]:
    import gspread
    gc = gspread.authorize(_creds(DRIVE_SCOPES))
    ws = gc.open_by_key(SHEET_ID).worksheet("Respuestas")
    rows = ws.get_all_values()
    result = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        nombre = row[1].strip() if len(row) > 1 else ""
        link = row[4].strip() if len(row) > 4 else ""
        if not nombre or not link:
            continue
        result.append({
            "nombre": nombre,
            "pregunta": row[3].strip() if len(row) > 3 else "",
            "link_audio": link,
            "transcripcion": row[5].strip() if len(row) > 5 else "",
        })
    return result


def download_from_drive(file_id: str, dest_path: str, creds):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    service = build("drive", "v3", credentials=creds)
    req = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()


def upload_to_gcs(local_path: str, blob_name: str, creds) -> str:
    from google.cloud import storage
    client = storage.Client(project=PROJECT, credentials=creds)
    bucket = client.bucket(AUDIO_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{AUDIO_BUCKET}/{blob_name}"


def update_firestore_link(doc_key: str, gcs_url: str, db_creds):
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT, credentials=db_creds)
    ref = db.collection("familias").document(FAMILIA_ID).collection("respuestas").document(doc_key)
    if ref.get().exists:
        ref.update({"link_audio": gcs_url})
        return True
    return False


def migrate(dry_run: bool = False):
    drive_creds = _creds(DRIVE_SCOPES)
    gcs_creds = _creds(GCS_SCOPES)
    fs_creds = _creds(FIRESTORE_SCOPES)

    rows = read_sheet_rows()
    print(f"\nTotal filas con audio: {len(rows)}")

    ok = 0
    skip = 0
    errors = 0

    for row in rows:
        nombre = row["nombre"]
        pregunta = row["pregunta"]
        url = row["link_audio"]

        # Si ya es GCS, saltar
        if url.startswith("gs://") or "storage.googleapis.com" in url:
            print(f"  [skip-gcs] {nombre} / {pregunta}")
            skip += 1
            continue

        file_id = _extract_drive_id(url)
        if not file_id:
            print(f"  [skip-no-id] {nombre} / {pregunta} — URL no reconocida: {url[:60]}")
            skip += 1
            continue

        nombre_key = _nombre_key(nombre)
        pregunta_key = _safe_key(pregunta) or "s_n"
        doc_key = f"{nombre_key}__{pregunta_key}"
        blob_name = f"{nombre_key}/{pregunta_key}/audio.webm"

        if dry_run:
            print(f"  [dry-run] {nombre} / {pregunta} → gs://{AUDIO_BUCKET}/{blob_name}")
            ok += 1
            continue

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            print(f"  [download] {nombre} / {pregunta} (Drive id={file_id[:12]}…)")
            download_from_drive(file_id, tmp_path, drive_creds)

            gcs_url = upload_to_gcs(tmp_path, blob_name, gcs_creds)
            print(f"  [uploaded] → {gcs_url}")

            updated = update_firestore_link(doc_key, gcs_url, fs_creds)
            if updated:
                print(f"  [firestore] link_audio actualizado en {doc_key}")
            else:
                print(f"  [firestore] doc {doc_key} no encontrado — corré seed primero")

            ok += 1

        except Exception as e:
            print(f"  [ERROR] {nombre} / {pregunta}: {e}")
            errors += 1

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    print(f"\n{'✅' if not dry_run else '🔍'} Migración {'completa' if not dry_run else 'simulada'}: {ok} ok / {skip} skip / {errors} errores")
    if errors:
        print("Los errores deben revisarse manualmente (posibles permisos de Drive).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migra audios Drive → GCS")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar qué se haría sin hacer nada")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
