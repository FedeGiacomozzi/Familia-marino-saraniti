"""
Acceso a GCS: descarga y subida de archivos para el pipeline.
"""

import datetime
import json
import os

from google.cloud import storage as gcs
from google.oauth2 import service_account

BUCKET_LIBROS = "libro-familiar-libros"

_SCOPES = [
    "https://www.googleapis.com/auth/devstorage.read_write",
    "https://www.googleapis.com/auth/iam",
]

_client_instance = None
_creds_instance = None


def _get_client() -> gcs.Client:
    global _client_instance, _creds_instance
    if _client_instance is not None:
        return _client_instance

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        _creds_instance = creds
        _client_instance = gcs.Client(credentials=creds, project=info["project_id"])
    else:
        _client_instance = gcs.Client()  # ADC fallback

    return _client_instance


def _get_creds():
    _get_client()
    return _creds_instance


def _parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    """'gs://bucket/path/to/file' → (bucket, blob_name)"""
    path = gcs_path.removeprefix("gs://")
    bucket, _, blob = path.partition("/")
    return bucket, blob


def download_file(gcs_path: str, dest_path: str):
    """Download a GCS object to a local file."""
    bucket_name, blob_name = _parse_gcs_path(gcs_path)
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)


def upload_libro(local_path: str, familia_id: str, filename: str) -> str:
    """Upload a PDF to libro-familiar-libros and return gs:// path."""
    client = _get_client()
    blob_name = f"{familia_id}/{filename}"
    bucket = client.bucket(BUCKET_LIBROS)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, content_type="application/pdf")
    return f"gs://{BUCKET_LIBROS}/{blob_name}"


def get_signed_url(gcs_path: str, expiration_days: int = 7) -> str:
    """Generate a signed URL. Max 7 days with SA credentials."""
    expiration_days = min(expiration_days, 7)
    bucket_name, blob_name = _parse_gcs_path(gcs_path)
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.generate_signed_url(
        expiration=datetime.timedelta(days=expiration_days),
        method="GET",
        credentials=_get_creds(),
        version="v4",
    )
