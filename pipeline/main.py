"""
main.py — FastAPI app para Cloud Run.

Endpoints:
  POST /run/pipeline      Pipeline completo (usa orchestrator internamente)
  POST /run/transcriber   Paso 1 aislado
  POST /run/voice         Paso 2 aislado
  POST /run/chapters      Paso 3 aislado (capítulos en paralelo)
  POST /run/editor        Paso 4 aislado  [pendiente]
  POST /run/layout        Paso 5 aislado  [pendiente]
  GET  /health
"""

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Pipeline API iniciada.")
    yield
    logger.info("Pipeline API detenida.")


app = FastAPI(title="Libro Familiar — Pipeline API", lifespan=lifespan)


# ── Request models ─────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nombres: list[str] | None = None
    pais: str | None = None
    solo_desde: str | None = None   # "voice" | "chapters" | "editor" | "layout"
    familia: str = "Familia"
    upload_to_drive: bool = False

class TranscriberRequest(BaseModel):
    row_indices: list[int] | None = None
    pais: str | None = None

class NombresRequest(BaseModel):
    nombres: list[str] | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run/pipeline")
def run_pipeline(req: PipelineRequest = PipelineRequest()):
    """Pipeline completo. El orchestrator coordina todos los pasos."""
    from agents.orchestrator import run
    try:
        result = run(
            nombres=req.nombres,
            pais=req.pais,
            solo_desde=req.solo_desde,
            familia=req.familia,
            upload_to_drive=req.upload_to_drive,
        )
        return {
            "ok": True,
            "transcriber": result.transcriber,
            "voice":       result.voice,
            "chapters":    result.chapters,
            "editor":      result.editor,
            "layout":      result.layout,
            "errores":     result.errores,
        }
    except Exception as e:
        logger.exception("Error en pipeline")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run/transcriber")
def run_transcriber(req: TranscriberRequest = TranscriberRequest()):
    from agents.transcriber import run
    try:
        result = run(row_indices=req.row_indices, pais=req.pais)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("Error en transcriber")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run/voice")
def run_voice(req: NombresRequest = NombresRequest()):
    from agents.voice_agent import run
    try:
        result = run(nombres=req.nombres)
        return {"ok": True, "protagonistas": list(result.keys()), "perfiles": result}
    except Exception as e:
        logger.exception("Error en voice_agent")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run/chapters")
def run_chapters(req: NombresRequest = NombresRequest()):
    from agents.chapter_agent import run
    try:
        result = run(nombres=req.nombres)
        return {
            "ok": True,
            "protagonistas": list(result.keys()),
            "palabras": {n: len(c.split()) for n, c in result.items()},
        }
    except Exception as e:
        logger.exception("Error en chapter_agent")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run/editor")
def run_editor(req: NombresRequest = NombresRequest()):
    from agents.editor_agent import run
    try:
        manuscript = run(nombres=req.nombres)
        return {
            "ok": True,
            "orden": manuscript.orden,
            "transiciones": list(manuscript.transiciones.keys()),
            "tokens_totales": manuscript.tokens_totales,
        }
    except Exception as e:
        logger.exception("Error en editor_agent")
        raise HTTPException(status_code=500, detail=str(e))


class LayoutRequest(BaseModel):
    familia: str = "Familia"
    nombres: list[str] | None = None
    upload_to_drive: bool = False

@app.post("/run/layout")
def run_layout(req: LayoutRequest = LayoutRequest()):
    from agents.editor_agent import run as _editor
    from agents.layout_agent import run as _layout
    try:
        manuscript = _editor(nombres=req.nombres)
        pdf_path = _layout(
            manuscript=manuscript,
            familia=req.familia,
            upload_to_drive=req.upload_to_drive,
        )
        return {"ok": True, "pdf": pdf_path}
    except Exception as e:
        logger.exception("Error en layout_agent")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
