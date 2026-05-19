"""
Cliente para leer y escribir en Google Sheets.

Sheet "Respuestas" — columnas (0-indexed):
  0 Fecha/hora | 1 Nombre | 2 FechaNac | 3 Pregunta | 4 LinkAudio | 5 Transcripción

Sheet "Perfiles" — columnas (0-indexed):
  0 Nombre | 1 FechaProcess | 2 PerfilVoz (JSON) | 3 TranscripciónCompleta | 4 Capítulo | 5 CapítuloRevisado
"""

import logging
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SHEET_ID = "1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM"

RESPUESTAS = "Respuestas"
PERFILES   = "Perfiles"

# Índices columnas "Respuestas"
COL_R_NOMBRE        = 1
COL_R_FECHA_NAC     = 2
COL_R_PREGUNTA      = 3
COL_R_LINK          = 4
COL_R_TRANSCRIPCION = 5
COL_R_FOTOGRAFIA    = 6

# Índices columnas "Perfiles"
COL_P_NOMBRE         = 0
COL_P_FECHA_PROCESS  = 1
COL_P_PERFIL_VOZ     = 2
COL_P_TRANSCRIPCION  = 3
COL_P_CAPITULO       = 4
COL_P_CAP_REVISADO   = 5

PERFILES_HEADERS = [
    "Nombre",
    "Fecha procesado",
    "Perfil Voz (JSON)",
    "Transcripción completa",
    "Capítulo",
    "Capítulo revisado",
]


class SheetsClient:
    def __init__(self, credentials):
        self._svc = build("sheets", "v4", credentials=credentials)
        self._sheets = self._svc.spreadsheets()

    # ── helpers internos ──────────────────────────────────────────────────────

    def _read_range(self, sheet_name: str, rng: str) -> list[list[Any]]:
        result = (
            self._sheets.values()
            .get(spreadsheetId=SHEET_ID, range=f"{sheet_name}!{rng}")
            .execute()
        )
        return result.get("values", [])

    def _write_cell(self, sheet_name: str, row: int, col: int, value: str) -> None:
        """row y col son 1-indexed (notación Sheets)."""
        col_letter = _col_letter(col)
        cell = f"{sheet_name}!{col_letter}{row}"
        self._sheets.values().update(
            spreadsheetId=SHEET_ID,
            range=cell,
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()
        logger.debug("Wrote %s → %s", cell, value[:60] if isinstance(value, str) else value)

    def _append_row(self, sheet_name: str, values: list[Any]) -> None:
        self._sheets.values().append(
            spreadsheetId=SHEET_ID,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()

    # ── Sheet "Respuestas" ────────────────────────────────────────────────────

    def get_respuestas(self) -> list[dict]:
        """
        Devuelve todas las filas de datos (sin header) como lista de dicts.
        Incluye 'row_index' (1-indexed, con header) para poder escribir de vuelta.
        """
        rows = self._read_range(RESPUESTAS, "A1:Z")
        if not rows:
            return []
        headers = rows[0]
        result = []
        for i, row in enumerate(rows[1:], start=2):  # start=2: fila 1 es header
            padded = row + [""] * (max(7, len(headers)) - len(row))
            result.append({
                "row_index":    i,
                "fecha":        padded[0],
                "nombre":       padded[COL_R_NOMBRE],
                "fecha_nac":    padded[COL_R_FECHA_NAC],
                "pregunta":     padded[COL_R_PREGUNTA],
                "link_audio":   padded[COL_R_LINK],
                "transcripcion": padded[COL_R_TRANSCRIPCION],
                "fotografia":   padded[COL_R_FOTOGRAFIA],
            })
        return result

    def write_transcripcion(self, row_index: int, transcripcion: str) -> None:
        self._write_cell(RESPUESTAS, row_index, COL_R_TRANSCRIPCION + 1, transcripcion)

    def get_foto_url(self, nombre: str) -> str:
        """Devuelve la URL de Drive de la foto de la persona, o '' si no tiene."""
        for row in self.get_respuestas():
            if row["nombre"].strip() == nombre and row["fotografia"].strip():
                return row["fotografia"].strip()
        return ""

    # ── Sheet "Perfiles" ──────────────────────────────────────────────────────

    def _ensure_perfiles_sheet(self) -> None:
        """Crea la hoja Perfiles con headers si no existe."""
        meta = self._sheets.get(spreadsheetId=SHEET_ID).execute()
        nombres = [s["properties"]["title"] for s in meta["sheets"]]
        if PERFILES not in nombres:
            body = {"requests": [{"addSheet": {"properties": {"title": PERFILES}}}]}
            self._sheets.batchUpdate(spreadsheetId=SHEET_ID, body=body).execute()
            self._append_row(PERFILES, PERFILES_HEADERS)
            logger.info("Hoja 'Perfiles' creada.")

    def get_perfiles(self) -> list[dict]:
        self._ensure_perfiles_sheet()
        rows = self._read_range(PERFILES, "A1:Z")
        if len(rows) <= 1:
            return []
        result = []
        for i, row in enumerate(rows[1:], start=2):
            padded = row + [""] * (6 - len(row))
            result.append({
                "row_index":       i,
                "nombre":          padded[COL_P_NOMBRE],
                "fecha_process":   padded[COL_P_FECHA_PROCESS],
                "perfil_voz":      padded[COL_P_PERFIL_VOZ],
                "transcripcion":   padded[COL_P_TRANSCRIPCION],
                "capitulo":        padded[COL_P_CAPITULO],
                "cap_revisado":    padded[COL_P_CAP_REVISADO],
            })
        return result

    def upsert_perfil(self, nombre: str, **fields) -> None:
        """
        Crea o actualiza la fila del protagonista en "Perfiles".
        'fields' puede tener: perfil_voz, transcripcion, capitulo, cap_revisado.
        """
        self._ensure_perfiles_sheet()
        perfiles = self.get_perfiles()
        existing = next((p for p in perfiles if p["nombre"] == nombre), None)

        col_map = {
            "perfil_voz":    COL_P_PERFIL_VOZ + 1,
            "transcripcion": COL_P_TRANSCRIPCION + 1,
            "capitulo":      COL_P_CAPITULO + 1,
            "cap_revisado":  COL_P_CAP_REVISADO + 1,
        }

        ts = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

        if existing:
            row_idx = existing["row_index"]
            self._write_cell(PERFILES, row_idx, COL_P_FECHA_PROCESS + 1, ts)
            for field, value in fields.items():
                if field in col_map:
                    self._write_cell(PERFILES, row_idx, col_map[field], str(value))
        else:
            row = [""] * 6
            row[COL_P_NOMBRE]        = nombre
            row[COL_P_FECHA_PROCESS] = ts
            for field, value in fields.items():
                idx = col_map.get(field)
                if idx:
                    row[idx - 1] = str(value)
            self._append_row(PERFILES, row)

        logger.info("Perfil actualizado: %s — campos: %s", nombre, list(fields.keys()))


def _col_letter(n: int) -> str:
    """Convierte número de columna 1-indexed a letra(s). Ej: 1→A, 27→AA."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result
