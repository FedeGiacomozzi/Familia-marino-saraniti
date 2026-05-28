import json
import os

from google.cloud import storage
from google.oauth2 import service_account

BUCKET_AUDIOS = "libro-familiar-audios"
BUCKET_FOTOS  = "libro-familiar-fotos"
BUCKET_LIBROS = "libro-familiar-libros"

_PROJECT_ID = "familia-marino"


def _get_creds():
    creds_json = os.environ.get("GCP_SA_KEY_JSON")
    if creds_json:
        info = json.loads(creds_json)
    else:
        path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "/secrets/credentials.json")
        with open(path) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _client() -> storage.Client:
    return storage.Client(credentials=_get_creds(), project=_PROJECT_ID)


# ─── Audios ───────────────────────────────────────────────────────────────────

def upload_audio(local_path: str, familia: str, filename: str) -> str:
    """Sube un audio y devuelve la ruta gs:// del objeto."""
    client = _client()
    blob_path = f"{familia}/{filename}"
    bucket = client.bucket(BUCKET_AUDIOS)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(local_path)
    return f"gs://{BUCKET_AUDIOS}/{blob_path}"


def download_audio(familia: str, filename: str, local_path: str):
    """Descarga un audio del bucket al path local."""
    client = _client()
    blob_path = f"{familia}/{filename}"
    bucket = client.bucket(BUCKET_AUDIOS)
    bucket.blob(blob_path).download_to_filename(local_path)


def list_audios(familia: str) -> list[str]:
    """Lista los nombres de archivo de audio para una familia."""
    client = _client()
    prefix = f"{familia}/"
    blobs = client.list_blobs(BUCKET_AUDIOS, prefix=prefix)
    return [b.name[len(prefix):] for b in blobs if b.name != prefix]


# ─── PDFs ─────────────────────────────────────────────────────────────────────

def upload_pdf(local_path: str, familia: str, filename: str) -> str:
    """Sube un PDF y devuelve la ruta gs:// del objeto."""
    client = _client()
    blob_path = f"{familia}/{filename}"
    bucket = client.bucket(BUCKET_LIBROS)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(local_path, content_type="application/pdf")
    return f"gs://{BUCKET_LIBROS}/{blob_path}"


# ─── Genérico ─────────────────────────────────────────────────────────────────

def file_exists(bucket_name: str, path: str) -> bool:
    """Devuelve True si el objeto existe en el bucket."""
    client = _client()
    return client.bucket(bucket_name).blob(path).exists()
