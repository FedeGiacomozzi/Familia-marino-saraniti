"""
Acceso a GCS: descarga de archivos para uso local en el pipeline.
"""

import json
import os

from google.cloud import storage as gcs
from google.oauth2 import service_account

_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_only"]

_client_instance = None


def _get_client() -> gcs.Client:
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        _client_instance = gcs.Client(credentials=creds, project=info["project_id"])
    else:
        _client_instance = gcs.Client()  # ADC fallback

    return _client_instance


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
