"""
Google Cloud Storage client — audio uploads for the recording flow.
"""

import json
import os
from datetime import timedelta
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

from pipeline.utils.secrets import get_secret

_client: Optional[storage.Client] = None

GCS_BUCKET = os.environ.get("GCS_BUCKET", "familia-pipeline-audios")
_SIGNED_URL_EXPIRY_DAYS = 7


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        creds_json = get_secret("GOOGLE_CREDENTIALS_JSON")
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        _client = storage.Client(project=creds_dict["project_id"], credentials=creds)
    return _client


def upload_audio(audio_bytes: bytes, blob_name: str, content_type: str = "audio/webm") -> str:
    """Upload audio bytes to GCS; returns the gs:// URI."""
    bucket = _get_client().bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(audio_bytes, content_type=content_type)
    return f"gs://{GCS_BUCKET}/{blob_name}"


def signed_url(blob_name: str) -> str:
    """Generate a 7-day signed HTTPS URL for a GCS object."""
    bucket = _get_client().bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    return blob.generate_signed_url(
        expiration=timedelta(days=_SIGNED_URL_EXPIRY_DAYS),
        method="GET",
        version="v4",
    )


def signed_url_from_gs_uri(gs_uri: str) -> str:
    """Convert a gs://bucket/blob URI to a signed HTTPS URL."""
    # gs://bucket/blob/name → blob/name
    without_prefix = gs_uri[len("gs://"):]
    _, blob_name = without_prefix.split("/", 1)
    return signed_url(blob_name)
