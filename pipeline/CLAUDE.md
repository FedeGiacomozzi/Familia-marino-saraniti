# Libro Familiar Automatizado — Contexto para Claude Code

## Tu rol en este proyecto
Sos el desarrollador Full Stack Senior. Tu coordinador es Claude (chat en claude.ai), que define qué se hace y en qué orden. Vos ejecutás. **Antes de arrancar cualquier sesión, leé Notion.**

## Cómo leer el contexto antes de arrancar
1. Abrí el MCP de Notion (ya está configurado)
2. Buscá la página: "📚 Libro Familiar — SO del proyecto"
3. Leé los Briefings activos (páginas con emoji 📋)
4. Revisá "✅ Pendientes" filtrado por Estado = "En curso"
5. Revisá "🐛 Bugs & Trabas" filtrado por Estado = "Abierto"

## Al terminar cada sesión (obligatorio)
Escribí en Notion DB "🗓️ Sesiones":
- Qué hiciste
- Qué archivos creaste o modificaste
- Qué bloqueantes encontraste
- Qué falta para completar el briefing

## Arquitectura actual

### Stack en producción (Fase 2)
- **Backend:** FastAPI en Google Cloud Run
- **Storage:** 3 buckets GCS (libro-familiar-audios, libro-familiar-fotos, libro-familiar-libros)
- **DB:** Firestore Native (colecciones: familias → integrantes → respuestas)
- **Secrets:** Google Secret Manager
- **Auth:** Service Account con roles Storage Object Admin + Cloud Datastore User
- **Región:** southamerica-east1

### Stack legacy (solo lectura, NO escribir)
- Google Drive folder: 1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3
- Google Sheet: 1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM
- Apps Script backend provisional (deprecado)

### Pipeline de agentes
```
main.py → FastAPI /admin (UI) + /run-pipeline
orchestrator.py → coordina 5 agentes en paralelo (5 threads)
transcriber.py → Whisper API → transcripción
chapter_agent.py → Claude Opus → ~3.500 palabras por persona
editor_agent.py → intro + cierre + transiciones
layout_agent.py → HTML A5, Playfair Display, paleta cálida
```

### Utils (estado de migración)
| Archivo | Estado | Descripción |
|---|---|---|
| utils/storage.py | 🔄 Migrar desde drive.py | GCS: upload, download, URLs firmadas |
| utils/firestore.py | 🔄 Migrar desde sheets.py | Firestore: CRUD familias/integrantes/respuestas |
| utils/migrate.py | ⏳ Crear | Script one-shot Drive→GCS, Sheets→Firestore |
| utils/tree.py | ✅ No tocar | SVG árbol genealógico |
| utils/pdf.py | ✅ No tocar | WeasyPrint HTML→PDF A5 |

## Modelo de datos Firestore

```
familias (collection)
└── {familia_id} (document)
    ├── nombre: str
    ├── comprador: {
    │     email: str,
    │     nombre: str,
    │     es_tambien_retratado: bool
    │   }
    ├── estado: "onboarding" | "grabando" | "generando" | "entregado"
    ├── pack: str
    ├── integrantes_extra: int
    ├── fecha_compra: timestamp
    ├── fecha_entrega: timestamp | null
    │
    └── integrantes (subcollection)
        └── {integrante_id} (document)
            ├── nombre: str
            ├── relacion_con_comprador: str
            ├── token_unico: str (UUID v4)
            ├── es_comprador: bool
            ├── estado: "pendiente" | "en_progreso" | "completo"
            ├── foto_url: str (gs://)
            ├── porcentaje_avance: int (0-100)
            ├── ultimo_acceso: timestamp
            │
            └── respuestas (subcollection)
                └── {pregunta_id} (document)
                    ├── audio_url: str (gs://)
                    ├── transcripcion: str
                    ├── duracion_seg: int
                    └── timestamp: timestamp
```

## Reglas de trabajo (no negociables)
1. **Nunca escribas en Drive ni en Sheets.** Solo lectura si necesitás referenciar datos legacy.
2. **Cada archivo que modifiques o crees, pasá el archivo completo.** No diffs parciales.
3. **No tomes decisiones de arquitectura.** Si encontrás algo que requiere una decisión, registralo en Notion DB "🧭 Decisiones" con estado "En evaluación" y avisá al coordinador.
4. **No corras migrate.py sin confirmación explícita** del coordinador.
5. **Registrá bugs** en Notion DB "🐛 Bugs & Trabas" antes de resolverlos.
6. **No modifiques** orchestrator.py, chapter_agent.py, editor_agent.py, layout_agent.py ni pdf.py hasta que storage.py y firestore.py estén validados.

## Variables de entorno requeridas (via Secret Manager)
```
OPENAI_API_KEY       # Whisper transcripción
ANTHROPIC_API_KEY    # Claude Opus capítulos
SHEET_ID             # legacy, solo lectura
ADMIN_PASSWORD       # panel /admin
GCS_BUCKET_AUDIOS=libro-familiar-audios
GCS_BUCKET_FOTOS=libro-familiar-fotos
GCS_BUCKET_LIBROS=libro-familiar-libros
FIRESTORE_PROJECT_ID # tu GCP project ID
```

## Producto (contexto para decisiones de UX)
- Target: familias LATAM, segmento regalo (cumpleaños 70, aniversarios)
- Distribución: digital LATAM, impreso Argentina
- Pack base: 4 integrantes. Upsell: +$8 USD por integrante extra
- Capítulos de menores: escritos por padres
- Árbol genealógico: generado automáticamente (tree.py), no lo completa el usuario
- Entrega: link firmado GCS de 30 días (no adjunto por mail)
- Links por integrante: únicos (UUID v4 como token), anti-duplicación

## 16 preguntas base (5 bloques)
1. De dónde venís — identidad, origen, infancia
2. Los que te formaron — familia de origen
3. Tus elecciones — trabajo, vida adulta, hobbies, logros
4. Tu gente — vínculos, amigos, travesuras
5. Lo que sabés ahora — legado, reflexión, mensaje a generaciones siguientes
