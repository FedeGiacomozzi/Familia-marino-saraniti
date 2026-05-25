import io
import json
import os
from datetime import timedelta

from google.cloud import storage
from google.oauth2 import service_account

PROJECT_ID = "familia-marino"
BUCKET_AUDIOS = "libro-familiar-audios"
BUCKET_FOTOS = "libro-familiar-fotos"
BUCKET_LIBROS = "libro-familiar-libros"

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _get_creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
    else:
        path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "/secrets/credentials.json")
        with open(path) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)


def _client() -> storage.Client:
    return storage.Client(project=PROJECT_ID, credentials=_get_creds())


def _parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    path = gcs_path.removeprefix("gs://")
    bucket_name, *parts = path.split("/")
    return bucket_name, "/".join(parts)


def upload_audio(
    familia_id: str,
    integrante_id: str,
    pregunta_id: str,
    file_bytes: bytes,
    content_type: str,
) -> str:
    client = _client()
    blob_name = f"{familia_id}/{integrante_id}/{pregunta_id}"
    client.bucket(BUCKET_AUDIOS).blob(blob_name).upload_from_string(file_bytes, content_type=content_type)
    return f"gs://{BUCKET_AUDIOS}/{blob_name}"


def upload_foto(
    familia_id: str,
    integrante_id: str,
    file_bytes: bytes,
    content_type: str,
) -> str:
    client = _client()
    ext = content_type.split("/")[-1] if "/" in content_type else "jpg"
    blob_name = f"{familia_id}/{integrante_id}/foto.{ext}"
    client.bucket(BUCKET_FOTOS).blob(blob_name).upload_from_string(file_bytes, content_type=content_type)
    return f"gs://{BUCKET_FOTOS}/{blob_name}"


def upload_libro(familia_id: str, version: str, file_bytes: bytes) -> str:
    client = _client()
    blob_name = f"{familia_id}/{version}/libro.pdf"
    client.bucket(BUCKET_LIBROS).blob(blob_name).upload_from_string(
        file_bytes, content_type="application/pdf"
    )
    return f"gs://{BUCKET_LIBROS}/{blob_name}"


def download_audio(gcs_path: str) -> bytes:
    client = _client()
    bucket_name, blob_name = _parse_gcs_path(gcs_path)
    return client.bucket(bucket_name).blob(blob_name).download_as_bytes()


def get_signed_url(gcs_path: str, expiration_minutes: int = 60) -> str:
    client = _client()
    bucket_name, blob_name = _parse_gcs_path(gcs_path)
    blob = client.bucket(bucket_name).blob(blob_name)
    return blob.generate_signed_url(
        expiration=timedelta(minutes=expiration_minutes),
        method="GET",
        version="v4",
    )


def get_libro_url(gcs_path: str, expiration_days: int = 30) -> str:
    return get_signed_url(gcs_path, expiration_minutes=expiration_days * 24 * 60)
