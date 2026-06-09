#!/usr/bin/env python3
"""
Migración de audios de Elba Marina Valladares Jiménez.
Drive → GCS → Firestore (respuestas + transcripciones) → perfil_voz.
Idempotente: si el blob ya existe en GCS, saltea la descarga.
"""
import io
import json
import os
import subprocess
import sys

from google.cloud import firestore as fs_module, storage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ─── Config ───────────────────────────────────────────────────────────────────

PROJECT_ID     = "familia-marino"
FAMILIA_ID     = "bracho-valladares"
INTEGRANTE_ID  = "elba-marina-valladares-jimenez"
NOMBRE         = "Elba Marina Valladares Jiménez"
BUCKET_AUDIOS  = "libro-familiar-audios"

DRIVE_LINKS = {
    1:  "1jF6RK2g-lp3THR8kMKNgyuqldaVzcIvf",
    2:  "1_hnneY6vbjoJsAPXusDzaRoQU5fl94Ek",
    3:  "1Gi3pDQ-AO6vYFjU1SkKeMubZYYKAkkgP",
    4:  "1yAMRIYBQQGgnAbq3EhHgBpgZTpPY6amp",
    5:  "1OACrnxgMcgR0wy0LRtAmhN3-Df0RYCtp",
    6:  "1N_368hESqB0B7msns-vTRAQkxXJdZ47n",
    7:  "1cCKGuQt-s8QWH3QG97AJq2Yr6L6Gk9B2",
    8:  "1uWasTCTqYeZzyeZ5RU8KabrMi5WC9Rvn",
    9:  "1bSPappb18PxFAhVX6VodlkV1g5UfdWL_",
    10: "1E9y_jEXwHcTxo9RL_ETgM9K6AvuEt8FF",
    11: "1hB1HpTAt5ZZHcLxIqzDRUN14KVSU4jXV",
    12: "1GhKaVCr7M69cpMuhwqdFS6wmnCdwGSer",
    13: "19co9VAz_SgfpSqHk7UFq1022V4xyBk8M",
    14: "16bWhNcCA7IDS4_pOSQMRPI-n42zOKh8k",
    15: "1PuuEcjjSWz2SupPZ2d6XE5cXbO-loefm",
    16: "1uRvrp9bfZ7c31K4S6_YpDqXtAkLootUA",
}

_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/cloud-platform",
]


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _load_creds():
    creds_json = subprocess.run(
        ["gcloud","secrets","versions","access","latest",
         "--secret=GOOGLE_CREDENTIALS","--project=familia-marino"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return service_account.Credentials.from_service_account_info(
        json.loads(creds_json), scopes=_SCOPES
    )


def _load_secret(name: str) -> str:
    return subprocess.run(
        ["gcloud","secrets","versions","access","latest",
         f"--secret={name}","--project=familia-marino"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


# ─── Drive download ───────────────────────────────────────────────────────────

def _download_from_drive(drive_svc, file_id: str) -> bytes:
    request = drive_svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Auth
    print("[auth] Cargando credenciales desde Secret Manager...")
    creds = _load_creds()
    db    = fs_module.Client(project=PROJECT_ID, credentials=creds)
    gcs   = storage.Client(project=PROJECT_ID, credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    if not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = _load_secret("OPENAI_API_KEY")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = _load_secret("ANTHROPIC_API_KEY")

    from openai import OpenAI
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    integrante_ref = (
        db.collection("familias").document(FAMILIA_ID)
          .collection("integrantes").document(INTEGRANTE_ID)
    )

    print(f"\n[gcs] Bucket: {BUCKET_AUDIOS}")
    print(f"[firestore] {FAMILIA_ID}/{INTEGRANTE_ID}\n")

    # ── Paso 1-4: por cada pregunta — Drive → GCS → Firestore → Whisper ───────
    results = {}
    for n, file_id in sorted(DRIVE_LINKS.items()):
        blob_name = f"{FAMILIA_ID}/{INTEGRANTE_ID}/q{n}.webm"
        gs_url    = f"gs://{BUCKET_AUDIOS}/{blob_name}"
        label     = f"q{n}"

        print(f"[{label}] file_id={file_id}")

        # Idempotencia: si el blob ya existe en GCS, saltear descarga
        blob = gcs.bucket(BUCKET_AUDIOS).blob(blob_name)
        if blob.exists():
            print(f"  GCS: ya existe — skipping download")
            audio_bytes = None
        else:
            try:
                audio_bytes = _download_from_drive(drive, file_id)
                blob.upload_from_string(audio_bytes, content_type="audio/webm")
                print(f"  GCS: subido {len(audio_bytes):,} bytes → {gs_url}")
            except Exception as e:
                print(f"  ERROR Drive/GCS: {e}")
                results[n] = {"error": str(e)}
                continue

        # Guardar audio_url en Firestore (merge para no pisar campos existentes)
        resp_ref = integrante_ref.collection("respuestas").document(str(n))
        resp_ref.set({"audio_url": gs_url}, merge=True)

        # Transcribir con Whisper (si ya tiene transcripción, saltear)
        resp_data = resp_ref.get().to_dict() or {}
        existing_tx = resp_data.get("transcripcion", "").strip()
        if existing_tx:
            print(f"  Whisper: ya tiene transcripción — skipping")
            results[n] = {"transcripcion": existing_tx}
            continue

        try:
            # Descargar bytes para Whisper si no los tenemos en memoria
            if audio_bytes is None:
                audio_bytes = blob.download_as_bytes()

            response = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=(f"q{n}.webm", io.BytesIO(audio_bytes)),
                language="es",
            )
            tx = response.text.strip()
            resp_ref.update({"transcripcion": tx})
            print(f"  Whisper: {tx[:80]}{'…' if len(tx) > 80 else ''}")
            results[n] = {"transcripcion": tx}
        except Exception as e:
            print(f"  ERROR Whisper: {e}")
            results[n] = {"error_whisper": str(e)}

    # ── Paso 5: actualizar estado del integrante ──────────────────────────────
    print("\n[firestore] Actualizando estado → completado, porcentaje_avance → 100")
    integrante_ref.update({
        "estado": "completado",
        "porcentaje_avance": 100,
    })

    # ── Paso 6: generar perfil_voz ────────────────────────────────────────────
    print("\n[voice_agent] Generando perfil_voz...")

    repo_root = os.path.dirname(os.path.abspath(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import anthropic
    from pipeline.agents.voice_agent import _build_perfil

    transcripciones = [
        {"pregunta": str(n), "transcripcion": v["transcripcion"]}
        for n, v in sorted(results.items())
        if v.get("transcripcion")
    ]

    if not transcripciones:
        print("  ERROR: sin transcripciones disponibles para generar perfil.")
    else:
        print(f"  Usando {len(transcripciones)} transcripciones...")
        claude_client = anthropic.Anthropic()
        perfil, tx_completa = _build_perfil(claude_client, NOMBRE, transcripciones)
        integrante_ref.update({
            "perfil_voz": perfil,
            "transcripcion_completa": tx_completa,
        })
        print("  ✓ perfil_voz guardado en Firestore")

    # ── Resumen final ─────────────────────────────────────────────────────────
    ok   = [n for n, v in results.items() if v.get("transcripcion")]
    errs = [n for n, v in results.items() if "error" in v or "error_whisper" in v]

    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Familia     : {FAMILIA_ID}")
    print(f"  Integrante  : {INTEGRANTE_ID}")
    print(f"  Audios OK   : {len(ok)}/{len(DRIVE_LINKS)}")
    print(f"  Errores     : {len(errs)}")
    print(f"  perfil_voz  : {'✓ generado' if transcripciones else '✗ pendiente'}")
    if errs:
        print(f"  Preguntas con error: {errs}")
    print("=" * 60)

    # Estado Firestore final
    print("\n[estado final en Firestore]")
    doc = integrante_ref.get().to_dict()
    print(f"  estado          : {doc.get('estado')}")
    print(f"  porcentaje_avance: {doc.get('porcentaje_avance')}")
    print(f"  perfil_voz      : {'✓ SI' if doc.get('perfil_voz') else '✗ NO'}")
    resp_docs = list(integrante_ref.collection("respuestas").stream())
    con_tx = sum(1 for r in resp_docs if r.to_dict().get("transcripcion","").strip())
    print(f"  respuestas c/tx : {con_tx}/{len(resp_docs)}")


if __name__ == "__main__":
    main()
