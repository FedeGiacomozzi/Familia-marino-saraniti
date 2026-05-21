"""
Genera un capítulo narrativo de 3200–3800 palabras por persona.
"""

import anthropic

from pipeline.utils import sheets

MODEL = "claude-opus-4-7"

_SYSTEM = """\
Sos un escritor literario especializado en memorias familiares y narrativa oral latinoamericana.
Tu trabajo es transformar transcripciones y perfiles de voz en capítulos de libro con prosa literaria,
manteniendo la autenticidad de cada persona.
"""

_PROMPT_TEMPLATE = """\
Escribí un capítulo narrativo de entre 3200 y 3800 palabras sobre {nombre}.

CONTEXTO FAMILIAR:
- Rol en la familia: {rol}
- Estado: {estado}
- Cónyuge/s: {conyuges}
- Hijos: {hijos}
- Padres: {padres}
- Hermanos (inferidos): {hermanos}

PERFIL DE VOZ:
- Muletillas: {muletillas}
- Frases propias: {frases_propias}
- Registro: {registro}
- Detalles sensoriales: {detalles_sensoriales}
- Tono: {tono}

TRANSCRIPCIÓN COMPLETA:
{transcripcion}

ARCO NARRATIVO SUGERIDO (no obligatorio, podés adaptarlo):
1. Apertura con imagen sensorial del lugar de origen
2. La infancia y quiénes lo/la formaron
3. Las elecciones que marcaron el camino
4. La vida que construyó — trabajo, familia, vínculos
5. Lo que sabe ahora que no sabía antes

REGLAS DE ESCRITURA:
- La voz del protagonista va en cursiva cuando aparece directamente, NUNCA entre comillas
- Integrá 2 o 3 de sus frases propias de forma orgánica, no forzada: {frases_propias_lista}
- No uses estas palabras: memorable, invaluable, legado, tesoro, entrañable, inmortal, huella
- Prosa fluida, párrafos de longitud variada
- Podés abrir con una cita directa o una imagen antes del nombre del protagonista
- El capítulo debe poder leerse solo, sin conocer a la persona previamente
- Usá el rol familiar ({rol}) como lente narrativo: cómo lo/la ven los demás, qué lugar ocupa en la trama familiar

Devolvé SOLO el texto del capítulo. Sin título. Sin notas. Sin explicaciones.
"""

_SKIP_MENOR = """\
Este capítulo pertenece a {nombre}, que es menor de edad.
El capítulo debe ser escrito en tercera persona por sus padres/tutores,
no generado automáticamente desde transcripciones propias.
"""


def generar_capitulo(client: anthropic.Anthropic, persona: dict) -> str:
    """
    persona dict esperado:
      nombre, perfil_voz (dict con los 7 campos), transcripcion,
      familia_ctx (optional dict from sheets.build_family_context)
    Returns empty string (with marker) if es_menor=True.
    """
    nombre = persona["nombre"]
    perfil = persona.get("perfil_voz", {})
    transcripcion = persona.get("transcripcion", "")
    fctx = persona.get("familia_ctx", {})

    # Skip chapter generation for minors
    if fctx.get("es_menor"):
        return f"[MENOR: {nombre} — capítulo a escribir por padres/tutores]"

    frases_propias = perfil.get("frases_propias", [])
    frases_propias_lista = ", ".join(f'"{f}"' for f in frases_propias[:5]) if frases_propias else "ninguna registrada"

    def _lista(items): return ", ".join(items) if items else "—"

    estado = "vive" if fctx.get("vive", True) else f"falleció el {fctx.get('fecha_fallec', 'fecha desconocida')}"

    prompt = _PROMPT_TEMPLATE.format(
        nombre=nombre,
        rol=fctx.get("rol", "no especificado"),
        estado=estado,
        conyuges=_lista(fctx.get("conyuges", [])),
        hijos=_lista(fctx.get("hijos", [])),
        padres=_lista(fctx.get("padres", [])),
        hermanos=_lista(fctx.get("hermanos", [])),
        muletillas=", ".join(perfil.get("muletillas", [])) or "no registradas",
        frases_propias=frases_propias_lista,
        registro=perfil.get("registro", "no registrado"),
        detalles_sensoriales=", ".join(perfil.get("detalles_sensoriales", [])) or "no registrados",
        tono=perfil.get("tono", "no registrado"),
        transcripcion=transcripcion[:80000],
        frases_propias_lista=frases_propias_lista,
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    capitulo = message.content[0].text.strip()
    sheets.save_chapter(nombre, capitulo)
    return capitulo


def run(nombres: list[str]) -> dict[str, str]:
    """Standalone: generate chapters for each nombre. Returns {nombre: capitulo_str}."""
    client = anthropic.Anthropic()
    results = {}

    for nombre in nombres:
        try:
            profile = sheets.get_profile(nombre)
            if not profile:
                raise ValueError(f"No hay perfil guardado para {nombre}")

            persona = {
                "nombre": nombre,
                "perfil_voz": profile.get("perfil_voz", {}),
                "transcripcion": profile.get("transcripcion", ""),
            }

            results[nombre] = generar_capitulo(client, persona)

        except Exception as e:
            print(f"[chapter_agent] Error con {nombre}: {e}")
            results[nombre] = f"ERROR: {e}"

    return results
