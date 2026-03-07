# Trading AI Agent – MVP (Windows System)

---

## 📌 Overview

Trading AI Agent is a modular, Windows-based AI system designed to:

* Continuously ingest financial news
* Analyze it locally using a GPU-hosted LLM
* Classify potential stock price impact
* Store historical results
* Trigger alerts for high-impact events

The system is intentionally designed using **isolated components with well-defined input and output contracts**, ensuring:

* Scalability
* Replaceable modules
* Clear integration boundaries
* Future microservice migration capability

---

# 🏗 High-Level Architecture

```text
News Provider
   ↓
News Ingestion Module
   ↓
Deduplication & Persistence Layer
   ↓
LLM Analysis Engine
   ↓
Impact Classification Layer
   ↓
Alerting System
   ↓
Storage & Logging
```

---

# 📰 News Provider

Source:

* GNews (free tier)

Role:

* Provide financial and market-related news articles.
* Return structured JSON responses.

---

# 🧠 AI Inference Stack

Framework:

* [Ollama](https://ollama.com/) — se eligió sobre Hugging Face Transformers por simplicidad: no requiere gestión de CUDA, cuantización ni dependencias de PyTorch.

Model:

* `qwen2.5:7b` (default, configurable via `MODEL_NAME` env var)

Execution Environment:

* Windows 10 / 11
* Local inference only (no external API calls)
* GPU opcional; Ollama gestiona la aceleración automáticamente

---

# 🔌 Module Isolation & Input–Output Contracts

Each component is designed as an isolated unit with clearly defined input and output schemas.

This ensures:

* Easy refactoring
* Replacement of API provider
* Replacement of LLM model
* Horizontal scaling in the future
* Potential microservice separation

---

## 1️⃣ News Ingestion Module

### Responsibility

Fetch news articles from external API.

### Input

* API credentials
* Query parameters (keywords, language, limit)

### Output (Standardized Article Object)

```text
Article {
    source: string
    title: string
    description: string
    content: string
    url: string
    published_at: datetime
}
```

### Contract Rules

* Always returns a list of standardized Article objects.
* No downstream module depends on raw API structure.
* API provider can be replaced without affecting the system.

---

## 2️⃣ Deduplication & Persistence Layer

### Responsibility

Prevent reprocessing of previously analyzed articles.

### Input

* Article object

### Internal Logic

* Check if `url` already exists in SQLite.
* If exists → skip.
* If not → allow processing.

### Output

```text
DeduplicationResult {
    is_new: boolean
    article: Article
}
```

### Contract Rules

* Downstream modules only receive articles marked as `is_new = true`.
* Database technology can be swapped (SQLite → PostgreSQL) without changing upstream logic.

---

## 3️⃣ LLM Analysis Engine

### Responsibility

Analyze news article for financial impact.

### Input

```text
AnalysisInput {
    title: string
    content: string
}
```

### Output (Structured JSON)

```text
LLMOutput {
    company: string
    impact_level: "LOW" | "MEDIUM" | "HIGH"
    impact_type: "POSITIVE" | "NEGATIVE" | "NEUTRAL"
    confidence: integer (0-100)
}
```

### Contract Rules

* Always returns structured output.
* No business logic embedded inside LLM module.
* LLM can be swapped (qwen2.5 → llama3 → any Ollama-supported model).
* Only text-in / structured-JSON-out.

---

## 4️⃣ Impact Classification Layer

### Responsibility

Decide whether to trigger alert.

### Input

* LLMOutput

### Logic

* If `impact_level == HIGH` → alert candidate.
* Optional future scoring:

  * Confidence threshold
  * Company whitelist
  * Sentiment bias weighting

### Output

```text
ClassificationResult {
    should_alert: boolean
    analysis: LLMOutput
}
```

### Contract Rules

* No email logic inside classifier.
* Pure decision engine.
* Easily extensible for ensemble models.

---

## 5️⃣ Alerting System

### Responsibility

Send email notification for high-impact news.

### Technology

* Gmail SMTP (App Password)

### Input

```text
AlertPayload {
    company: string
    impact_level: string
    impact_type: string
    confidence: integer
    title: string
    url: string
}
```

### Output

```text
AlertResult {
    sent: boolean
    timestamp: datetime
}
```

### Contract Rules

* Email system is isolated.
* Can be replaced by:

  * Slack notifications
  * Telegram bot
  * REST webhook
  * Kafka producer

---

## 6️⃣ Storage & Logging Layer

### Responsibility

Persist analysis results.

### Input

* Article object
* LLMOutput
* AlertResult

### Stored Fields

* URL (unique)
* Title
* Company
* Impact Level
* Impact Type
* Confidence
* Alert Sent Flag
* Timestamp

### Output

* Confirmation of successful persistence

### Contract Rules

* Persistence separated from classification.
* Enables future:

  * Backtesting
  * Statistical analysis
  * Model retraining
  * Performance monitoring

---

# 📊 Scalability Strategy

Because each module:

* Has defined input/output schemas
* Has no cross-layer dependency leakage
* Avoids shared global state

The system can evolve into:

* Distributed microservices
* Queue-based architecture (Kafka / RabbitMQ)
* REST-based inference service
* Multi-model ensemble inference
* Real-time streaming pipeline

---

# 🗺 Implementation Planning

See plan.md

# 🚀 Future Evolution

* Embedding-based similarity filtering
* Multi-LLM consensus scoring
* Real-time stock price correlation
* Dashboard visualization
* REST API service mode
* Horizontal scaling
* Distributed processing nodes

---
