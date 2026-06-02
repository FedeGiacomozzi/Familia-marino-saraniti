# Familia Libro Pipeline — Guía de desarrollo

## Cloud Run

- **URL producción**: `https://familia-pipeline-776445604502.us-central1.run.app`
- **Servicio**: `familia-pipeline` en `us-central1`
- **Deploy**: `./deploy.sh [PROJECT_ID]`

## Stack

| Capa | Tecnología |
|------|-----------|
| API | FastAPI + uvicorn (Python 3.12) |
| Transcripción | OpenAI Whisper |
| Generación | Anthropic Claude Opus |
| PDF | WeasyPrint (A5) |
| Datos (legacy) | Google Sheets + Drive |
| Datos (nuevo) | Firestore (jobs + tokens) |
| Audio | Google Cloud Storage (`GCS_BUCKET`) |
| Infraestructura | Cloud Run, Secret Manager |

## Estructura del proyecto

```
pipeline/
  main.py                 # FastAPI endpoints
  agents/
    orchestrator.py       # Coordina los 5 pasos
    transcriber.py        # Whisper → transcripción
    voice_agent.py        # Perfil de voz (Claude)
    chapter_agent.py      # Capítulo por persona (Claude)
    editor_agent.py       # Orden + transiciones + prólogo/epílogo (Claude)
    layout_agent.py       # PDF con WeasyPrint
  utils/
    sheets.py             # Google Sheets + Drive
    secrets.py            # Secret Manager
    firestore_client.py   # Firestore: jobs y tokens de grabación
    gcs_client.py         # GCS: subida de audios
```

## Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/run/pipeline` | Pipeline completo (síncrono, para admin panel) |
| POST | `/run/pipeline/async` | Pipeline asíncrono → devuelve `job_id` |
| GET | `/job/{job_id}` | Estado del job; incluye `pdf_url` firmada cuando está done |
| POST | `/audio/{token}` | Recibe audio grabado, guarda en GCS, marca token, auto-trigger |
| POST | `/run/transcriber` | Solo paso 1 |
| POST | `/run/voice` | Solo paso 2 |
| POST | `/run/chapters` | Solo paso 3 |
| POST | `/run/editor` | Solo paso 4 |
| POST | `/run/layout` | Solo paso 5 |

## Modelo de datos Firestore

```
jobs/{job_id}
  status: "pending" | "done" | "error"
  familia_id: str
  nombres: [str]
  created_at: ISO datetime
  completed_at: ISO datetime
  pdf_url: gs:// URI  (done)
  error_msg: str       (error)

familias/{familia_id}
  nombre: str
  pais: str
  /tokens/{token_id}
    nombre: str
    completado: bool
    audio_url: gs:// URI
    completado_at: ISO datetime
```

## Variables de entorno / secretos

| Nombre | Fuente |
|--------|--------|
| `ANTHROPIC_API_KEY` | Secret Manager |
| `OPENAI_API_KEY` | Secret Manager |
| `GOOGLE_CREDENTIALS_JSON` | Secret Manager (`GOOGLE_CREDENTIALS:latest`) |
| `GCS_BUCKET_AUDIOS` | Env var (default: `libro-familiar-audios`) |
| `GCS_BUCKET_FOTOS` | Env var (default: `libro-familiar-fotos`) |
| `GCS_BUCKET_LIBROS` | Env var (default: `libro-familiar-libros`) |

## Auto-trigger

Cuando el último integrante de una familia graba su audio vía `POST /audio/{token}`,
el endpoint marca el token en Firestore y llama `_check_y_trigger`. Si no quedan tokens
pendientes, se lanza automáticamente un job asíncrono del pipeline completo.
