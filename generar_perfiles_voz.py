#!/usr/bin/env python3
"""
Script one-shot: genera perfiles de voz faltantes para integrantes
de la familia marino-saraniti que ya tienen transcripciones en Firestore.

Uso:
  python generar_perfiles_voz.py
"""
import json
import os
import subprocess
import sys

# ─── Auth: SA desde Secret Manager ───────────────────────────────────────────

def _load_sa_creds():
    """Carga la SA desde Secret Manager y la devuelve como Credentials."""
    from google.oauth2 import service_account

    # 1. Intentar desde env var (ya seteada en sesiones anteriores)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    # 2. Si no, buscar desde Secret Manager vía gcloud CLI
    if not creds_json:
        try:
            result = subprocess.run(
                [
                    "gcloud", "secrets", "versions", "access", "latest",
                    "--secret=GOOGLE_CREDENTIALS",
                    "--project=familia-marino",
                ],
                capture_output=True, text=True, check=True,
            )
            creds_json = result.stdout.strip()
            print("[auth] SA cargada desde Secret Manager.")
        except subprocess.CalledProcessError as e:
            print(f"[auth] Error accediendo al secret: {e.stderr}")
            sys.exit(1)

    info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def _load_api_key(secret_name: str) -> str:
    """Lee una API key desde Secret Manager."""
    result = subprocess.run(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            f"--secret={secret_name}",
            "--project=familia-marino",
        ],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ─── Lógica principal ─────────────────────────────────────────────────────────

FAMILIA_ID = "marino-saraniti"
PROJECT_ID = "familia-marino"

# Nombres objetivo (slugified como pueden aparecer en Firestore)
TARGETS = [
    "jose-antonio-bracho-zarraga",
    "mariela-valeria-mariño",
    "mariela-valeria-mari-o",
    "marlene-valladares-jim-nez",
    "valentin-mariño",
    "valentin-mari-o",
]


def _slug(nombre: str) -> str:
    """Normaliza nombre a slug para comparación."""
    import unicodedata
    s = nombre.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(" ", "-")
    return s


def main():
    # ── Cargar credenciales ───────────────────────────────────────────────────
    creds = _load_sa_creds()

    # ── Setear ANTHROPIC_API_KEY ──────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[auth] Cargando ANTHROPIC_API_KEY desde Secret Manager...")
        os.environ["ANTHROPIC_API_KEY"] = _load_api_key("ANTHROPIC_API_KEY")
        print("[auth] ANTHROPIC_API_KEY cargada.")

    # ── Conectar a Firestore con la SA ────────────────────────────────────────
    from google.cloud import firestore as fs_module
    db = fs_module.Client(project=PROJECT_ID, credentials=creds)

    # ── Listar todos los integrantes de marino-saraniti ───────────────────────
    print(f"\n[firestore] Listando integrantes de '{FAMILIA_ID}'...")
    integrantes_docs = (
        db.collection("familias")
        .document(FAMILIA_ID)
        .collection("integrantes")
        .stream()
    )

    integrantes = []
    for doc in integrantes_docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        integrantes.append(data)

    print(f"  Total integrantes: {len(integrantes)}")
    for i in integrantes:
        tiene_perfil = bool(i.get("perfil_voz"))
        tiene_transcripcion = bool(i.get("transcripcion_completa", "").strip())
        print(
            f"  ID={i['_id']!r:40s}  nombre={i.get('nombre','')!r:35s}"
            f"  perfil_voz={'SI' if tiene_perfil else 'NO':3s}"
            f"  transcripcion_completa={'SI' if tiene_transcripcion else 'NO'}"
        )

    # ── Identificar cuáles procesar ───────────────────────────────────────────
    target_slugs = {_slug(t) for t in TARGETS}

    to_process = []
    for i in integrantes:
        doc_id_slug = _slug(i["_id"])
        nombre_slug = _slug(i.get("nombre", ""))

        if doc_id_slug in target_slugs or nombre_slug in target_slugs:
            if i.get("perfil_voz"):
                print(f"\n  SKIP {i['_id']!r}: ya tiene perfil_voz.")
                continue
            to_process.append(i)

    if not to_process:
        print("\nNo hay integrantes objetivo sin perfil_voz. Todo listo.")
        return

    print(f"\n[proceso] {len(to_process)} integrante(s) para generar perfil_voz:")
    for i in to_process:
        print(f"  - {i['_id']} ({i.get('nombre','')})")

    # ── Agregar repo al path ──────────────────────────────────────────────────
    repo_root = os.path.dirname(os.path.abspath(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import anthropic
    from pipeline.agents.voice_agent import _build_perfil

    client = anthropic.Anthropic()

    results = {}
    for integrante in to_process:
        integrante_id = integrante["_id"]
        nombre = integrante.get("nombre") or integrante_id
        print(f"\n[voice] Procesando: {nombre} ({integrante_id})")

        # ── Leer transcripciones desde subcollection respuestas ───────────────
        respuestas_docs = (
            db.collection("familias")
            .document(FAMILIA_ID)
            .collection("integrantes")
            .document(integrante_id)
            .collection("respuestas")
            .order_by("__name__")
            .stream()
        )

        transcripciones = []
        for rdoc in respuestas_docs:
            rdata = rdoc.to_dict()
            texto = rdata.get("transcripcion", "").strip()
            if texto:
                transcripciones.append({"pregunta": rdoc.id, "transcripcion": texto})

        # Fallback: transcripcion_completa guardada en el doc del integrante
        if not transcripciones and integrante.get("transcripcion_completa", "").strip():
            transcripciones = [{"pregunta": "completa", "transcripcion": integrante["transcripcion_completa"]}]

        if not transcripciones:
            print(f"  ERROR: sin transcripciones para {nombre}. Saltando.")
            results[integrante_id] = {"error": "sin transcripciones"}
            continue

        print(f"  Transcripciones encontradas: {len(transcripciones)}")

        # ── Generar perfil de voz ─────────────────────────────────────────────
        try:
            perfil, transcripcion_completa = _build_perfil(client, nombre, transcripciones)
        except Exception as e:
            print(f"  ERROR generando perfil: {e}")
            results[integrante_id] = {"error": str(e)}
            continue

        # ── Guardar en Firestore ──────────────────────────────────────────────
        (
            db.collection("familias")
            .document(FAMILIA_ID)
            .collection("integrantes")
            .document(integrante_id)
            .update({
                "perfil_voz": perfil,
                "transcripcion_completa": transcripcion_completa,
            })
        )
        print(f"  OK: perfil_voz guardado para {nombre}.")
        results[integrante_id] = {"ok": True, "nombre": nombre}

    # ── Resumen final ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    ok = [v for v in results.values() if v.get("ok")]
    err = [v for v in results.values() if v.get("error")]
    print(f"  Perfiles generados exitosamente : {len(ok)}")
    print(f"  Errores                         : {len(err)}")
    for integrante_id, v in results.items():
        if v.get("ok"):
            print(f"  ✓ {integrante_id} ({v.get('nombre','')})")
        else:
            print(f"  ✗ {integrante_id}: {v.get('error')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
