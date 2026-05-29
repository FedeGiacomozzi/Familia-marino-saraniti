"""
Carga secrets desde Google Secret Manager.
En local, lee variables de entorno como fallback (para desarrollo sin Cloud Run).
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "familia-marino")


def _gcp_available() -> bool:
    try:
        from google.cloud import secretmanager  # noqa: F401
        return True
    except ImportError:
        return False


def get_secret(secret_name: str) -> str:
    """
    Devuelve el valor del secret. Orden de búsqueda:
    1. Variable de entorno con el mismo nombre (útil en local)
    2. Google Secret Manager (Cloud Run)
    """
    env_val = os.environ.get(secret_name)
    if env_val:
        return env_val

    if not _gcp_available():
        raise RuntimeError(
            f"Secret '{secret_name}' no encontrado en env y google-cloud-secret-manager no está instalado."
        )

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{_PROJECT_ID}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("UTF-8")


def get_google_credentials():
    """
    Devuelve credenciales de Service Account para las APIs de Google.
    Soporta JSON directo en el secret o path a archivo local.
    """
    from google.oauth2 import service_account

    cred_raw = get_secret("GCP_SA_KEY_JSON")

    # Puede ser JSON string o path a archivo
    try:
        info = json.loads(cred_raw)
    except (json.JSONDecodeError, ValueError):
        with open(cred_raw) as f:
            info = json.load(f)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)
