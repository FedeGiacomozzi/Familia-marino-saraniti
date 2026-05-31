#!/usr/bin/env python3
"""
Transcribe los audios pendientes del Sheet (sistema legado Drive → Sheets).

Uso:
  # Transcribir filas específicas (los índices los da check_sheet.py):
  export OPENAI_API_KEY="sk-..."
  export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
  python3 pipeline/scripts/transcribe_sheet.py --filas 3 7 12 18

  # Transcribir todos los que faltan automáticamente:
  python3 pipeline/scripts/transcribe_sheet.py --auto

  # Solo una persona:
  python3 pipeline/scripts/transcribe_sheet.py --auto --nombre "Ignacio"
"""

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filas",   nargs="+", type=int, help="Índices de fila del Sheet (1-based)")
    parser.add_argument("--auto",    action="store_true",  help="Detectar automáticamente los pendientes")
    parser.add_argument("--nombre",  default=None,          help="Filtrar por nombre (con --auto)")
    parser.add_argument("--pais",    default="argentina",   help="País para vocabulario")
    args = parser.parse_args()

    if not args.filas and not args.auto:
        parser.print_help()
        sys.exit(1)

    from pipeline.utils import sheets
    from pipeline.agents.transcriber import run as transcribir

    if args.auto:
        print("Leyendo Sheet para detectar pendientes...")
        rows = sheets.get_all_rows()
        data = rows[1:]
        indices = []
        for i, row in enumerate(data, start=2):
            nombre    = row[sheets.COL_NOMBRE      - 1].strip() if len(row) >= sheets.COL_NOMBRE       else ""
            audio_url = row[sheets.COL_LINK_AUDIO  - 1].strip() if len(row) >= sheets.COL_LINK_AUDIO   else ""
            transc    = row[sheets.COL_TRANSCRIPCION-1].strip() if len(row) >= sheets.COL_TRANSCRIPCION else ""
            if audio_url and not transc:
                if args.nombre is None or nombre.lower() == args.nombre.lower():
                    indices.append(i)
        if not indices:
            print("No hay filas pendientes de transcripción.")
            return
        print(f"Filas pendientes encontradas: {indices}")
    else:
        indices = args.filas

    print(f"\nTranscribiendo {len(indices)} audio(s)... (pais={args.pais})\n")
    result = transcribir(row_indices=indices, pais=args.pais)
    print(f"\nResultado: procesadas={result['procesadas']}, errores={result['errores']}")


if __name__ == "__main__":
    main()
