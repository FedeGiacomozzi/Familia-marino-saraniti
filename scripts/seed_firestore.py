#!/usr/bin/env python3
"""
seed_firestore.py — Carga inicial de datos desde Google Sheets a Firestore.

Lee:
  - Sheet "Respuestas" (SHEET_ID): nombre, pregunta, link_audio, foto, fecha_nac
  - Sheet "Perfiles"   (SHEET_ID): perfil_voz JSON, transcripcion, capitulo
  - Sheet "Integrantes" (FAMILIA_SHEET_ID): nombre, fecha_nac, fecha_fallec, rol, es_menor
  - Sheet "Relaciones"  (FAMILIA_SHEET_ID): persona_a, relacion, persona_b

Escribe todo a Firestore en la familia FAMILIA_ID (default: marino-saraniti).

Usage (desde la raíz del repo, con venv activo):
  FAMILIA_ID=marino-saraniti \\
  GCP_SA_KEY_JSON="$(cat /path/to/key.json)" \\
  python scripts/seed_firestore.py [--dry-run]
"""

import argparse
import json
import os
import sys

# ── Leer Sheets sin dependencias del pipeline ─────────────────────────────────
import gspread
from google.oauth2 import service_account

SHEET_ID = "1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM"
FAMILIA_SHEET_ID = "1iEpnly_f3OQL6nLH41XU76zg1iM2vHZQyQdF0RLVQFE"

SCOPES_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _sheets_creds():
    raw = os.environ.get("GCP_SA_KEY_JSON", "")
    if not raw:
        raise SystemExit("Falta GCP_SA_KEY_JSON en el entorno")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        with open(raw) as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES_SHEETS)


def _gc():
    return gspread.authorize(_sheets_creds())


def _ws(sheet_id: str, tab: str):
    return _gc().open_by_key(sheet_id).worksheet(tab)


# ── Leer datos del Sheet ───────────────────────────────────────────────────────

def read_respuestas() -> list[dict]:
    try:
        ws = _ws(SHEET_ID, "Respuestas")
    except Exception as e:
        print(f"[warn] No se pudo leer tab Respuestas: {e}")
        return []
    rows = ws.get_all_values()
    result = []
    for row in rows[1:]:  # skip header
        if not any(c.strip() for c in row):
            continue
        nombre = row[1].strip() if len(row) > 1 else ""
        if not nombre:
            continue
        result.append({
            "nombre": nombre,
            "fecha_nac": row[2].strip() if len(row) > 2 else "",
            "pregunta": row[3].strip() if len(row) > 3 else "",
            "link_audio": row[4].strip() if len(row) > 4 else "",
            "transcripcion": row[5].strip() if len(row) > 5 else "",
            "foto_url": row[6].strip() if len(row) > 6 else "",
        })
    return result


def read_perfiles() -> list[dict]:
    try:
        ws = _ws(SHEET_ID, "Perfiles")
    except Exception as e:
        print(f"[warn] No se pudo leer tab Perfiles: {e}")
        return []
    rows = ws.get_all_values()
    result = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        nombre = row[0].strip() if len(row) > 0 else ""
        if not nombre:
            continue
        perfil_str = row[2].strip() if len(row) > 2 else ""
        try:
            perfil_voz = json.loads(perfil_str) if perfil_str else {}
        except json.JSONDecodeError:
            perfil_voz = {}
        result.append({
            "nombre": nombre,
            "fecha_process": row[1].strip() if len(row) > 1 else "",
            "perfil_voz": perfil_voz,
            "transcripcion": row[3].strip() if len(row) > 3 else "",
            "capitulo": row[4].strip() if len(row) > 4 else "",
            "capitulo_revisado": row[5].strip() if len(row) > 5 else "",
        })
    return result


def read_integrantes() -> list[dict]:
    try:
        ws = _ws(FAMILIA_SHEET_ID, "Integrantes")
    except Exception as e:
        print(f"[warn] No se pudo leer tab Integrantes: {e}")
        return []
    rows = ws.get_all_values()
    result = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        nombre = row[0].strip() if len(row) > 0 else ""
        if not nombre:
            continue
        fecha_fallec = row[2].strip() if len(row) > 2 else ""
        es_menor_raw = row[4].strip().lower() if len(row) > 4 else ""
        result.append({
            "nombre": nombre,
            "fecha_nac": row[1].strip() if len(row) > 1 else "",
            "fecha_fallec": fecha_fallec,
            "rol": row[3].strip().lower() if len(row) > 3 else "",
            "es_menor": es_menor_raw in ("sí", "si", "s", "yes"),
            "vive": not bool(fecha_fallec),
        })
    return result


def read_relaciones() -> list[dict]:
    try:
        ws = _ws(FAMILIA_SHEET_ID, "Relaciones")
    except Exception as e:
        print(f"[warn] No se pudo leer tab Relaciones: {e}")
        return []
    rows = ws.get_all_values()
    result = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        pa = row[0].strip() if len(row) > 0 else ""
        rel = row[1].strip().lower() if len(row) > 1 else ""
        pb = row[2].strip() if len(row) > 2 else ""
        if pa and rel and pb:
            result.append({"persona_a": pa, "relacion": rel, "persona_b": pb})
    return result


# ── Escribir a Firestore ───────────────────────────────────────────────────────

def _nombre_key(nombre: str) -> str:
    return nombre.strip().lower().replace(" ", "_")


def seed(dry_run: bool = False):
    from google.cloud import firestore
    from google.oauth2 import service_account as sa_mod

    raw = os.environ.get("GCP_SA_KEY_JSON", "")
    project = os.environ.get("GCP_PROJECT_ID", "familia-marino")
    familia_id = os.environ.get("FAMILIA_ID", "marino-saraniti")

    if not raw:
        raise SystemExit("Falta GCP_SA_KEY_JSON")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        with open(raw) as f:
            info = json.load(f)

    creds = sa_mod.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/datastore"]
    )
    db = firestore.Client(project=project, credentials=creds)
    familia_ref = db.collection("familias").document(familia_id)

    tag = "[DRY-RUN]" if dry_run else "[write]"

    # ── Integrantes ────────────────────────────────────────────────────────────
    integrantes = read_integrantes()
    print(f"\n── Integrantes ({len(integrantes)}) ──")
    for p in integrantes:
        key = _nombre_key(p["nombre"])
        data = {**p, "nombre_key": key}
        print(f"  {tag} integrantes/{key}")
        if not dry_run:
            familia_ref.collection("integrantes").document(key).set(data, merge=True)

    # ── Relaciones ─────────────────────────────────────────────────────────────
    relaciones = read_relaciones()
    print(f"\n── Relaciones ({len(relaciones)}) ──")
    # Borramos las existentes para evitar duplicados en re-runs
    if not dry_run:
        existing = familia_ref.collection("relaciones").stream()
        for d in existing:
            d.reference.delete()
    for r in relaciones:
        key = f"{_nombre_key(r['persona_a'])}__{r['relacion']}__{_nombre_key(r['persona_b'])}"
        print(f"  {tag} relaciones/{key}")
        if not dry_run:
            familia_ref.collection("relaciones").document(key).set(r)

    # ── Perfiles ───────────────────────────────────────────────────────────────
    perfiles = read_perfiles()
    print(f"\n── Perfiles ({len(perfiles)}) ──")
    for p in perfiles:
        key = _nombre_key(p["nombre"])
        data = {**p, "nombre_key": key}
        print(f"  {tag} perfiles/{key}  (capitulo: {len(p['capitulo'])} chars)")
        if not dry_run:
            familia_ref.collection("perfiles").document(key).set(data, merge=True)

    # ── Respuestas ─────────────────────────────────────────────────────────────
    respuestas = read_respuestas()
    print(f"\n── Respuestas ({len(respuestas)}) ──")
    for r in respuestas:
        key_nombre = _nombre_key(r["nombre"])
        pregunta_key = r["pregunta"].replace(" ", "_").replace("/", "_")[:30] or "s_n"
        doc_key = f"{key_nombre}__{pregunta_key}"
        data = {**r, "nombre_key": key_nombre}
        audio_tag = "✓ audio" if r["link_audio"] else "— sin audio"
        trans_tag = f"✓ trans({len(r['transcripcion'])})" if r["transcripcion"] else "— sin trans"
        print(f"  {tag} respuestas/{doc_key}  [{audio_tag}] [{trans_tag}]")
        if not dry_run:
            familia_ref.collection("respuestas").document(doc_key).set(data, merge=True)

    print(f"\n{'✅ Seed completo.' if not dry_run else '✅ Dry-run completo — nada fue escrito.'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Firestore desde Google Sheets")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar qué se escribiría sin escribir")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)
