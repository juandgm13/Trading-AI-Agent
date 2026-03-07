# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trading AI Agent is a Python pipeline that ingests financial news, analyzes it locally using an LLM via Ollama, classifies stock impact, and sends email alerts. Runs on Windows.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Configuration via `.env` file (never commit this):
- `GNEWS_API_KEY` – GNews API key
- `GMAIL_USER` / `GMAIL_APP_PASSWORD` – Gmail SMTP credentials

## Running

```bash
python main.py
```

For automation, configure Windows Task Scheduler to run `main.py` on a schedule.

## Architecture

The system is a **linear pipeline** where each module has strict input/output contracts and no cross-layer dependency leakage:

```
GNews API → News Ingestion → Deduplication (SQLite) → LLM Analysis → Impact Classification → Alerting (Gmail SMTP) → Storage
```

### Module Contracts

| Module | Input | Output |
|---|---|---|
| **News Ingestion** | API credentials + query params | `Article { source, title, description, content, url, published_at }` |
| **Deduplication** | `Article` | `DeduplicationResult { is_new: bool, article: Article }` |
| **LLM Analysis** | `{ title, content }` | `LLMOutput { company, impact_level: LOW/MEDIUM/HIGH, impact_type: POSITIVE/NEGATIVE/NEUTRAL, confidence: 0-100 }` |
| **Impact Classification** | `LLMOutput` | `ClassificationResult { should_alert: bool, analysis: LLMOutput }` |
| **Alerting** | `AlertPayload` | `AlertResult { sent: bool, timestamp }` |
| **Storage** | `Article + LLMOutput + AlertResult` | Persistence confirmation |

### Key Design Constraints

- Each module is **independently replaceable** (e.g., swap GNews → Bloomberg, qwen2.5 → llama3, Gmail → Slack).
- No business logic inside the LLM module — it is text-in / structured-JSON-out only.
- The classifier is a pure decision engine with no notification logic.
- SQLite deduplication uses `url` as the unique key to prevent reprocessing.

## Tech Stack

- **LLM**: `qwen2.5:7b` via [Ollama](https://ollama.com/) (local inference, sin GPU obligatorio)
  - Se eligió Ollama en lugar de `transformers` + `bitsandbytes` por simplicidad: no requiere gestión manual de CUDA, cuantización ni dependencias de PyTorch.
  - El modelo se puede cambiar con la variable de entorno `MODEL_NAME`.
- **News source**: GNews free tier
- **Database**: SQLite (standard library)
- **Alerts**: Gmail SMTP with App Password
- **Dependencies**: `ollama`, `requests`, `python-dotenv`

## Implementation Phases (Plan.md)

1. `requirements.txt` + virtual environment
2. Article schema + SQLite persistence + deduplication
3. LLM loading + structured JSON prompt engineering
4. Email alert integration + end-to-end validation
5. Logging, error handling, Windows Task Scheduler automation
