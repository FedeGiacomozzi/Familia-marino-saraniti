"""
Orchestrator: coordina los 5 agentes del pipeline de extremo a extremo.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime

from pipeline.agents import (
    chapter_agent,
    editor_agent,
    layout_agent,
    transcriber,
    voice_agent,
)
from pipeline.agents.editor_agent import BookManuscript
from pipeline.utils import firestore as db
from pipeline.utils import storage

STEPS = ["transcriber", "voice", "chapters", "editor", "layout"]


@dataclass
class PipelineResult:
    personas: list[str] = field(default_factory=list)
    transcriber: dict = field(default_factory=dict)
    voice: dict[str, dict] = field(default_factory=dict)
    chapters: dict[str, str] = field(default_factory=dict)
    editor: BookManuscript | None = None
    layout: str = ""
    errores: list[str] = field(default_factory=list)
    _manuscript: BookManuscript | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return len(self.errores) == 0


def _build_personas_meta(
    nombres: list[str],
    integrantes: list[dict],
    relaciones: list[dict],
) -> list[dict]:
    meta = []
    for nombre in nombres:
        integrante = next(
            (p for p in integrantes if p["nombre"].lower() == nombre.lower()), {}
        )
        fecha_nac = integrante.get("fecha_nac") or db.get_fecha_nac(nombre)
        ctx = db.build_family_context(nombre, integrantes, relaciones)
        meta.append(
            {
                "nombre": nombre,
                "fecha_nac": fecha_nac,
                "rol": integrante.get("rol", ""),
                "fecha_fallec": integrante.get("fecha_fallec", ""),
                "vive": integrante.get("vive", True),
                "es_menor": integrante.get("es_menor", False),
                "familia_ctx": ctx,
            }
        )
    return meta


def run(
    nombres: list[str],
    pais: str = "argentina",
    solo_desde: str | None = None,
    familia: str = "Familia Mariño · Saraniti",
    upload_to_gcs: bool = True,
    familia_id: str | None = None,
) -> PipelineResult:
    """
    Corre el pipeline completo (o desde un paso específico).
    solo_desde: uno de "transcriber", "voice", "chapters", "editor", "layout"
    upload_to_gcs: si True, sube el PDF a GCS al final.
    """
    result = PipelineResult(personas=nombres)

    start_idx = 0
    if solo_desde and solo_desde in STEPS:
        start_idx = STEPS.index(solo_desde)

    # ── Cargar datos de familia ───────────────────────────────────────────────
    try:
        integrantes = db.get_familia_integrantes(familia_id)
        relaciones = db.get_familia_relaciones(familia_id)
        fallecidos = db.get_fallecidos(integrantes)
    except Exception as e:
        print(f"[orchestrator] Advertencia: no se pudieron cargar datos de familia: {e}")
        integrantes, relaciones, fallecidos = [], [], []

    personas_meta = _build_personas_meta(nombres, integrantes, relaciones)

    menores = [p["nombre"] for p in personas_meta if p.get("es_menor")]
    adultos = [p["nombre"] for p in personas_meta if not p.get("es_menor")]

    if menores:
        print(f"[orchestrator] Menores detectados (sin capítulo automático): {menores}")

    # ── Paso 1: Transcriber ───────────────────────────────────────────────────
    if start_idx <= 0:
        print("[orchestrator] Paso 1: transcripción de audios...")
        try:
            result.transcriber = transcriber.run(pais=pais, nombre=None, solo_pendientes=True)
            print(f"  → {result.transcriber}")
        except Exception as e:
            result.errores.append(f"transcriber: {e}")
            return result

    # ── Paso 2: Voice agent ───────────────────────────────────────────────────
    if start_idx <= 1:
        print("[orchestrator] Paso 2: análisis de voz...")
        try:
            result.voice = voice_agent.run(adultos)
            errores_voz = [n for n, v in result.voice.items() if "error" in v]
            if errores_voz:
                result.errores.append(f"voice_agent falló para: {errores_voz}")
        except Exception as e:
            result.errores.append(f"voice_agent: {e}")
            return result

    # ── Paso 3: Chapter agent ─────────────────────────────────────────────────
    if start_idx <= 2:
        print("[orchestrator] Paso 3: generación de capítulos...")
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic()

            for pm in personas_meta:
                nombre = pm["nombre"]
                if pm.get("es_menor"):
                    result.chapters[nombre] = f"[MENOR: {nombre} — capítulo a escribir por padres/tutores]"
                    continue

                p = db.get_profile(nombre)
                if not p:
                    result.errores.append(f"chapter_agent/{nombre}: perfil no encontrado en Firestore")
                    continue

                persona = {
                    "nombre": nombre,
                    "perfil_voz": p.get("perfil_voz", {}),
                    "transcripcion": p.get("transcripcion", ""),
                    "familia_ctx": pm.get("familia_ctx", {}),
                }
                try:
                    cap = chapter_agent.generar_capitulo(client, persona)
                    result.chapters[nombre] = cap
                except Exception as e:
                    result.errores.append(f"chapter_agent/{nombre}: {e}")

        except Exception as e:
            result.errores.append(f"chapter_agent: {e}")
            return result

    # ── Paso 4: Editor agent ──────────────────────────────────────────────────
    if start_idx <= 3:
        print("[orchestrator] Paso 4: edición del manuscrito...")
        try:
            if start_idx > 2:
                # Cargamos capítulos desde Firestore si se saltó el paso 3
                for pm in personas_meta:
                    nombre = pm["nombre"]
                    p = db.get_profile(nombre)
                    if p and p.get("capitulo"):
                        result.chapters[nombre] = p["capitulo"]

            editor_personas = [
                {
                    "nombre": pm["nombre"],
                    "fecha_nac": pm["fecha_nac"],
                    "perfil_voz": pm.get("familia_ctx", {}),
                }
                for pm in personas_meta
                if not pm.get("es_menor") and pm["nombre"] in result.chapters
            ]

            result.editor = editor_agent.run(
                personas=editor_personas,
                capitulos={k: v for k, v in result.chapters.items() if not v.startswith("[MENOR")},
                relaciones=relaciones,
                fallecidos=fallecidos,
            )
            result._manuscript = result.editor

        except Exception as e:
            result.errores.append(f"editor_agent: {e}")
            return result

    # ── Paso 5: Layout agent ──────────────────────────────────────────────────
    if start_idx <= 4:
        print("[orchestrator] Paso 5: generación del PDF...")
        try:
            manuscript = result._manuscript or result.editor
            if manuscript is None:
                raise ValueError("No hay manuscrito disponible para el layout")

            capitulo_personas = [pm for pm in personas_meta if not pm.get("es_menor")]

            todos_integrantes = [
                {
                    "nombre": p["nombre"],
                    "fecha_nac": p.get("fecha_nac", ""),
                    "fecha_fallec": p.get("fecha_fallec", ""),
                    "rol": p.get("rol", ""),
                    "vive": p.get("vive", True),
                }
                for p in integrantes
            ] if integrantes else capitulo_personas

            pdf_path = layout_agent.run(
                manuscript=manuscript,
                personas_meta=capitulo_personas,
                nombre_familia=familia,
                todos_integrantes=todos_integrantes,
            )
            result.layout = pdf_path
            print(f"  → PDF generado: {pdf_path}")

            if upload_to_gcs and pdf_path:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"libro_{familia_id or db.FAMILIA_ID}_{ts}.pdf"
                gcs_url = storage.upload_pdf(pdf_path, filename)
                print(f"  → Subido a GCS: {gcs_url}")
                result.layout = gcs_url

        except Exception as e:
            result.errores.append(f"layout_agent: {e}")

    return result
