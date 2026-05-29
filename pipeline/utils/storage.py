"""
Cliente GCS para el pipeline familiar.
Reemplaza las operaciones de Drive de sheets.py.

Buckets esperados:
  AUDIO_BUCKET  — libro-familiar-audios    (audios crudos)
  PDF_BUCKET    — libro-familiar-pdfs      (PDFs generados)
  FOTO_BUCKET   — libro-familiar-fotos     (fotos de integrantes)
"""

import json
import os
import re
import tempfile
from functools import lru_cache

from google.cloud import storage as gcs
from google.oauth2 import service_account

_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "familia-marino")
AUDIO_BUCKET = os.environ.get("AUDIO_BUCKET", "libro-familiar-audios")
PDF_BUCKET = os.environ.get("PDF_BUCKET", "libro-familiar-pdfs")
FOTO_BUCKET = os.environ.get("FOTO_BUCKET", "libro-familiar-fotos")


@lru_cache(maxsize=1)
def _client() -> gcs.Client:
    cred_raw = os.environ.get("GCP_SA_KEY_JSON")
    if cred_raw:
        try:
            info = json.loads(cred_raw)
        except (json.JSONDecodeError, ValueError):
            with open(cred_raw) as f:
                info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        return gcs.Client(project=_PROJECT_ID, credentials=creds)
    return gcs.Client(project=_PROJECT_ID)


# ─── Descarga ─────────────────────────────────────────────────────────────────

def _gcs_path_from_url(url: str) -> tuple[str, str]:
    """
    Convierte una URL GCS o path a (bucket, blob_name).
    Soporta:
      gs://bucket/path
      https://storage.googleapis.com/bucket/path
      solo el path dentro del bucket (sin prefijo)
    """
    if url.startswith("gs://"):
        parts = url[5:].split("/", 1)
        return parts[0], parts[1]
    m = re.match(r"https://storage\.googleapis\.com/([^/]+)/(.+)", url)
    if m:
        return m.group(1), m.group(2)
    # Asumimos path relativo dentro de AUDIO_BUCKET
    return AUDIO_BUCKET, url


def download_file(url_or_path: str, dest_path: str, bucket_hint: str | None = None):
    """
    Descarga un archivo desde GCS a dest_path.
    url_or_path puede ser gs://, https://storage.googleapis.com/ o blob name.
    """
    bucket_name, blob_name = _gcs_path_from_url(url_or_path)
    if bucket_hint and not url_or_path.startswith("gs://") and not url_or_path.startswith("http"):
        bucket_name = bucket_hint
    bucket = _client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)


def download_audio(url_or_path: str, dest_path: str):
    """Descarga un audio desde GCS (usa AUDIO_BUCKET por defecto)."""
    download_file(url_or_path, dest_path, bucket_hint=AUDIO_BUCKET)


def download_foto(url_or_path: str, dest_path: str):
    """Descarga una foto desde GCS (usa FOTO_BUCKET por defecto)."""
    download_file(url_or_path, dest_path, bucket_hint=FOTO_BUCKET)


# ─── Subida ───────────────────────────────────────────────────────────────────

def upload_pdf(local_path: str, filename: str, public: bool = True) -> str:
    """
    Sube un PDF al bucket de PDFs.
    Retorna la URL pública o gs:// si no es público.
    """
    bucket = _client().bucket(PDF_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_filename(local_path, content_type="application/pdf")
    if public:
        blob.make_public()
        return blob.public_url
    return f"gs://{PDF_BUCKET}/{filename}"


def upload_file(
    local_path: str,
    bucket_name: str,
    blob_name: str,
    content_type: str = "application/octet-stream",
    public: bool = False,
) -> str:
    bucket = _client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, content_type=content_type)
    if public:
        blob.make_public()
        return blob.public_url
    return f"gs://{bucket_name}/{blob_name}"


# ─── Listado ──────────────────────────────────────────────────────────────────

def list_audios(prefix: str = "") -> list[str]:
    """Lista blobs en AUDIO_BUCKET con prefijo opcional. Retorna gs:// paths."""
    bucket = _client().bucket(AUDIO_BUCKET)
    blobs = bucket.list_blobs(prefix=prefix)
    return [f"gs://{AUDIO_BUCKET}/{b.name}" for b in blobs]


def list_pdfs(prefix: str = "") -> list[str]:
    bucket = _client().bucket(PDF_BUCKET)
    blobs = bucket.list_blobs(prefix=prefix)
    return [f"gs://{PDF_BUCKET}/{b.name}" for b in blobs]


# ─── Compatibilidad con sheets.download_drive_file ───────────────────────────

def download_drive_file(url: str, dest_path: str):
    """
    Alias de download_file para compatibilidad con código que importaba sheets.
    Intenta detectar si es URL de Drive (legacy) o GCS.
    Si es Drive, lanza error claro.
    """
    if "drive.google.com" in url or "docs.google.com" in url:
        raise ValueError(
            f"URL de Drive detectada: {url!r}. "
            "Los archivos deben estar migrados a GCS. "
            "Usá el script de migración Drive→GCS."
        )
    download_file(url, dest_path)
