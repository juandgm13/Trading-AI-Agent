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
