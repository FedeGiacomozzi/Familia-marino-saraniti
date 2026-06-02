#!/usr/bin/env python3
"""
Transcribe los audios pendientes de Firestore + GCS.

Uso:
  export OPENAI_API_KEY="sk-..."
  export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'   # o usa ADC
  python3 pipeline/scripts/transcribe_pending.py

Argumentos opcionales:
  --pais      argentina | uruguay | chile | mexico | ... (default: argentina)
  --solo      nombre de persona (solo procesa esa persona)
  --dry-run   muestra los pendientes sin transcribir
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Agregar el root del proyecto al path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Transcribe audios pendientes")
    parser.add_argument("--pais",    default="argentina", help="País para vocabulario Whisper")
    parser.add_argument("--solo",    default=None,        help="Solo procesar esta persona")
    parser.add_argument("--dry-run", action="store_true", help="Solo listar pendientes, no transcribir")
    args = parser.parse_args()

    # Verificar claves necesarias
    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: falta OPENAI_API_KEY")
        sys.exit(1)

    from pipeline.utils.firestore import get_respuestas_sin_transcribir, save_transcripcion

    print("Conectando a Firestore...")
    pendientes = get_respuestas_sin_transcribir()

    if args.solo:
        pendientes = [p for p in pendientes if p.get("nombre", "").lower() == args.solo.lower()]

    if not pendientes:
        print("No hay audios pendientes de transcripción.")
        return

    print(f"\n{'='*60}")
    print(f"Audios pendientes: {len(pendientes)}")
    print(f"{'='*60}")
    for p in pendientes:
        nombre   = p.get("nombre", "?")
        pregunta = p.get("pregunta", p.get("pregunta_num", "?"))
        uri      = p.get("audio_uri", "?")
        print(f"  • {nombre:20s}  pregunta {pregunta}  →  {uri}")

    if args.dry_run:
        print("\n[dry-run] No se transcribió nada.")
        return

    print(f"\nIniciando transcripción con Whisper (pais={args.pais})...")
    print(f"{'='*60}\n")

    from openai import OpenAI
    from google.cloud import storage as gcs

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    gcs_client = gcs.Client()

    vocab_hints = {
        "argentina": "che, boludo, pibe, laburo, quilombo, copado, macanudo, bondi",
        "uruguay":   "che, gurí, pila, ta, farra, rambla, candombe",
        "chile":     "huevón, cachai, po, fome, bacán, pololo, carrete",
        "colombia":  "parcero, bacano, chimba, chévere, vaina, rumba",
        "mexico":    "güey, chido, neta, órale, chamba, cuate",
        "españa":    "tío, mola, guay, vale, hostia, curro, chaval",
    }
    hints = vocab_hints.get(args.pais.lower(), "familia, recuerdos, infancia")
    prompt = (
        f"Transcripción en español. Vocabulario regional: {hints}. "
        "Incluir muletillas y expresiones coloquiales tal como se dicen."
    )

    ok = 0
    err = 0

    for resp in pendientes:
        doc_id   = resp["_id"]
        nombre   = resp.get("nombre", "?")
        pregunta = resp.get("pregunta", resp.get("pregunta_num", "?"))
        uri      = resp["audio_uri"]

        ext = ".webm"
        for candidate in (".mp3", ".ogg", ".wav", ".m4a", ".mp4", ".webm"):
            if uri.lower().endswith(candidate):
                ext = candidate
                break

        print(f"→ {nombre} / pregunta {pregunta} ... ", end="", flush=True)

        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_path = tmp.name

            try:
                # Descargar desde GCS
                if uri.startswith("gs://"):
                    parts = uri[5:].split("/", 1)
                    bucket_name, blob_name = parts[0], parts[1]
                    gcs_client.bucket(bucket_name).blob(blob_name).download_to_filename(tmp_path)
                else:
                    import urllib.request
                    urllib.request.urlretrieve(uri, tmp_path)

                file_size = os.path.getsize(tmp_path)
                print(f"descargado ({file_size//1024} KB) ... ", end="", flush=True)

                # Transcribir con Whisper
                with open(tmp_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="es",
                        prompt=prompt,
                    )

                transcripcion = result.text.strip()
                save_transcripcion(doc_id, transcripcion)
                ok += 1
                print(f"OK ({len(transcripcion)} chars)")
                print(f"   \"{transcripcion[:120]}{'...' if len(transcripcion) > 120 else ''}\"")

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            err += 1
            print(f"ERROR: {e}")

    print(f"\n{'='*60}")
    print(f"Transcripciones completadas: {ok}")
    if err:
        print(f"Errores:                    {err}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
