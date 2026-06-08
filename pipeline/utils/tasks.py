"""
Cloud Tasks helper — encola jobs del pipeline hacia /run/pipeline/worker.
"""
import json
import os

from google.cloud import tasks_v2

PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "familia-marino")
LOCATION   = os.environ.get("CLOUD_TASKS_LOCATION", "southamerica-east1")
QUEUE      = os.environ.get("CLOUD_TASKS_QUEUE", "pipeline-jobs")
CLOUD_RUN_URL = os.environ.get(
    "CLOUD_RUN_URL",
    "https://familia-pipeline-776445604502.southamerica-east1.run.app",
)

_client = None


def _tasks_client() -> tasks_v2.CloudTasksClient:
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def enqueue_pipeline(job_id: str, req_dict: dict) -> str:
    """
    Crea un task en pipeline-jobs que hace POST a /run/pipeline/worker.
    Retorna el task_name asignado por Cloud Tasks.
    """
    client = _tasks_client()
    parent = client.queue_path(PROJECT_ID, LOCATION, QUEUE)

    payload = {"job_id": job_id, **req_dict}

    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{CLOUD_RUN_URL}/run/pipeline/worker",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode(),
        )
    )

    response = client.create_task(
        request=tasks_v2.CreateTaskRequest(parent=parent, task=task)
    )
    return response.name
