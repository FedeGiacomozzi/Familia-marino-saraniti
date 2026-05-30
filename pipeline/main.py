"""Minimal test — isolate Cloud Run startup issue."""
import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
_log = logging.getLogger(__name__)

_log.info("STEP 1: importing fastapi")
from fastapi import FastAPI
_log.info("STEP 2: fastapi OK")

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok", "step": "minimal"}

_log.info("STEP 3: app ready — testing imports")

try:
    _log.info("STEP 4: importing weasyprint")
    from weasyprint import HTML, CSS
    _log.info("STEP 4: weasyprint OK")
except Exception as e:
    _log.exception(f"STEP 4 FAILED: weasyprint: {e}")

try:
    _log.info("STEP 5: importing agents")
    from pipeline.agents import orchestrator, transcriber, voice_agent, chapter_agent, layout_agent
    _log.info("STEP 5: agents OK")
except Exception as e:
    _log.exception(f"STEP 5 FAILED: agents: {e}")

try:
    _log.info("STEP 6: importing firestore")
    from pipeline.utils import firestore as db
    _log.info("STEP 6: firestore OK")
except Exception as e:
    _log.exception(f"STEP 6 FAILED: firestore: {e}")

try:
    _log.info("STEP 7: importing storage")
    from pipeline.utils import storage
    _log.info("STEP 7: storage OK")
except Exception as e:
    _log.exception(f"STEP 7 FAILED: storage: {e}")

_log.info("STEP 8: all done")
