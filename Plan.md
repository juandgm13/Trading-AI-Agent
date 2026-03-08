# 🗺 Implementation Planning

## Phase 1 – Dependency Definition

Create `requirements.txt` defining:

* Ollama (local LLM inference — replaces Transformers/bitsandbytes for simplicity, no CUDA setup required)
* Requests
* Python-dotenv
* SQLite (standard library)

Install using:

* pip installation from requirements file
* Virtual environment isolation

Deliverable:
Reproducible dependency setup using a single installation command.

---

## Phase 2 – Core Infrastructure

* Implement standardized Article object schema
* Implement SQLite persistence layer
* Implement deduplication logic
* Validate input-output contracts

Deliverable:
Stable ingestion and storage pipeline.

---

## Phase 3 – LLM Integration

* Connect to local Ollama server (replaces Hugging Face Transformers for simplicity)
* Default model: `qwen2.5:7b` (configurable via `MODEL_NAME` env var)
* Implement structured financial analysis prompt
* Enforce strict JSON output schema
* Validate structured parsing

Deliverable:
Reliable AI impact detection engine.

---

## Phase 4.1 – Web UI (Streamlit)

Objetivo: interfaz visual extensible para inspeccionar y gestionar los datos del agente.

Tecnología elegida: **Streamlit** — Python puro, sin frontend separado, fácil de extender con nuevas páginas/secciones.

Funcionalidades iniciales:

* Tabla con todos los artículos analizados (título, empresa, ticker, impact level/type, confianza, fecha)
* Filtros por `impact_level` e `impact_type`
* Botón para borrar un artículo individual (por URL)
* Botón para resetear toda la base de datos

Estructura:

```
ui/
└── app.py          # Entrada Streamlit (st.set_page_config + navegación)
└── pages/
    └── articles.py # Vista principal de artículos
```

Arrancar con:

```bash
streamlit run ui/app.py
```

Deliverable:
Dashboard local funcional que consume directamente `StorageLayer`.

---

## Phase 4.2 – LLM Market-scope Classification

Objetivo: que el LLM clasifique el alcance de la noticia en el campo `company`, en lugar de devolver siempre `Unknown` para noticias de mercado general.

Valores posibles para `company`:

| Valor | Cuándo usarlo |
|---|---|
| `<nombre empresa>` | Noticia principalmente sobre una empresa específica |
| `MARKET` | Afecta al mercado general (Fed, inflación, PIB, índices bursátiles, eventos macro) |
| `SECTOR:<nombre>` | Afecta a un sector entero (ej. `SECTOR:TECH`, `SECTOR:ENERGY`, `SECTOR:BANKING`) |
| `Unknown` | No se puede determinar el afectado (debe ser raro) |

Cambios:

* Actualizar `SYSTEM_PROMPT` en `src/llm.py` con las reglas de clasificación de scope.
* Cuando `company` es `MARKET` o `SECTOR:*`, el `ticker` es siempre `N/A`.

Deliverable:
Noticias macro y sectoriales correctamente categorizadas en lugar de `Unknown`.

---

## Phase 4 – Alert Integration

* Implement isolated email notification module
* Integrate classification logic
* Validate end-to-end flow
* Test alert triggering

Deliverable:
Automated notification system.

---

## Phase 5 – Automation & Stability

* Add execution logging
* Add error handling
* Configure Windows Task Scheduler
* Optimize inference performance
* Validate long-term runtime stability

Deliverable:
Autonomous, continuously running financial news agent.

---
