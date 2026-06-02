#!/usr/bin/env python3
"""
Muestra el estado de transcripciones en el Sheet.
No toca nada — solo lee y reporta.

Uso:
  export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
  python3 pipeline/scripts/check_sheet.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.utils import sheets

print("Conectando al Sheet...")
rows = sheets.get_all_rows()

if not rows:
    print("El Sheet está vacío.")
    sys.exit(0)

header = rows[0]
data   = rows[1:]

print(f"\nTotal de filas (sin header): {len(data)}")
print(f"Columnas: {header}\n")
print(f"{'#':<4} {'Nombre':<20} {'Pregunta':<10} {'Audio':<6} {'Transcripción'}")
print("-" * 72)

sin_audio        = []
sin_transcripcion = []

for i, row in enumerate(data, start=2):   # fila real en el Sheet (1-based, +header)
    nombre    = row[sheets.COL_NOMBRE      - 1].strip() if len(row) >= sheets.COL_NOMBRE      else ""
    pregunta  = row[sheets.COL_PREGUNTA    - 1].strip() if len(row) >= sheets.COL_PREGUNTA    else ""
    audio_url = row[sheets.COL_LINK_AUDIO  - 1].strip() if len(row) >= sheets.COL_LINK_AUDIO  else ""
    transc    = row[sheets.COL_TRANSCRIPCION-1].strip() if len(row) >= sheets.COL_TRANSCRIPCION else ""

    tiene_audio  = "✓" if audio_url  else "✗"
    tiene_transc = f"{len(transc)} chars" if transc else "✗ FALTA"

    print(f"{i:<4} {nombre:<20} {pregunta:<10} {tiene_audio:<6} {tiene_transc}")

    if not audio_url:
        sin_audio.append((i, nombre, pregunta))
    elif not transc:
        sin_transcripcion.append((i, nombre, pregunta))

print("-" * 72)
print(f"\nResumen:")
print(f"  Total filas:              {len(data)}")
print(f"  Con audio + transcripción: {len(data) - len(sin_audio) - len(sin_transcripcion)}")
print(f"  Sin transcripción (falta): {len(sin_transcripcion)}")
print(f"  Sin audio (vacías):        {len(sin_audio)}")

if sin_transcripcion:
    print(f"\nFilas para transcribir (índices de Sheet):")
    indices = [i for i, _, _ in sin_transcripcion]
    for i, nombre, pregunta in sin_transcripcion:
        print(f"  fila {i} — {nombre} / pregunta {pregunta}")
    print(f"\nPara transcribir estas {len(indices)} filas:")
    print(f"  python3 pipeline/scripts/transcribe_sheet.py --filas {' '.join(map(str, indices))}")
