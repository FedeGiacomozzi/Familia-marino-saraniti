# Health Check Plan

## Current endpoint

`GET /health` returns `{"status": "ok"}` immediately — no dependency checks.

---

## Proposed: `GET /health/deep`

A deep health check that verifies all external dependencies are reachable and accessible with the configured credentials.

### Checks to implement

| Check | What it tests | Pass condition |
|---|---|---|
| Sheets read | Open `SHEET_ID`, read first row | No exception, ≥1 row returned |
| Familia sheet read | Open `FAMILIA_SHEET_ID`, read first row | No exception |
| Drive list | List files in `FOLDER_ID` | No 403/404, `files` key in response |
| Drive download | Download the first file found in `FOLDER_ID` (1 byte) | No exception |
| Anthropic ping | `client.models.list()` or minimal message | HTTP 200 |
| OpenAI ping | `client.models.list()` | HTTP 200 |

### Implementation sketch

```python
@app.get("/health/deep")
def health_deep():
    results = {}

    # Sheets
    try:
        ws = sheets.respuestas_sheet()
        ws.row_values(1)
        results["sheets_respuestas"] = "ok"
    except Exception as e:
        results["sheets_respuestas"] = f"ERROR: {e}"

    try:
        ws = sheets.integrantes_sheet()
        ws.row_values(1)
        results["sheets_familia"] = "ok"
    except Exception as e:
        results["sheets_familia"] = f"ERROR: {e}"

    # Drive folder access
    try:
        from googleapiclient.discovery import build
        creds = sheets._get_creds()
        service = build("drive", "v3", credentials=creds)
        resp = service.files().list(
            q=f"'{sheets.FOLDER_ID}' in parents",
            pageSize=1,
            fields="files(id,name)",
        ).execute()
        results["drive_folder"] = f"ok ({len(resp.get('files', []))} files visible)"
    except Exception as e:
        results["drive_folder"] = f"ERROR: {e}"

    # Anthropic
    try:
        import anthropic
        anthropic.Anthropic().models.list()
        results["anthropic"] = "ok"
    except Exception as e:
        results["anthropic"] = f"ERROR: {e}"

    # OpenAI / Whisper
    try:
        import openai
        openai.OpenAI().models.list()
        results["openai"] = "ok"
    except Exception as e:
        results["openai"] = f"ERROR: {e}"

    all_ok = all(v == "ok" or v.startswith("ok") for v in results.values())
    return {"status": "ok" if all_ok else "degraded", "checks": results}
```

### Response example (all passing)

```json
{
  "status": "ok",
  "checks": {
    "sheets_respuestas": "ok",
    "sheets_familia": "ok",
    "drive_folder": "ok (12 files visible)",
    "anthropic": "ok",
    "openai": "ok"
  }
}
```

### Response example (Drive issue)

```json
{
  "status": "degraded",
  "checks": {
    "sheets_respuestas": "ok",
    "sheets_familia": "ok",
    "drive_folder": "ERROR: 403 The caller does not have permission",
    "anthropic": "ok",
    "openai": "ok"
  }
}
```

---

## Usage

Run before each deploy to verify all dependencies are accessible:

```bash
curl https://familia-pipeline-rxvtynuftq-uc.a.run.app/health/deep | jq
```

The `drive_folder` check specifically validates that the Service Account
(`familia-pipeline@familia-marino.iam.gserviceaccount.com`) has read access
to the folder where audio files are stored — the most common failure mode.
