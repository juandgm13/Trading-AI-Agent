# Architecture

## Multi-agent state machine

Each symbol has an independent state machine managed by `PriceMonitor`. Additionally, a global market context report is refreshed every `analysis_interval_hours` and shared across all symbols.

```mermaid
stateDiagram-v2
    direction LR

    [*] --> WATCHING

    WATCHING --> OPPORTUNITY_FOUND : BUY signal + favorable market context
    WATCHING --> WATCHING : HOLD / REJECT or interval not due

    OPPORTUNITY_FOUND --> POSITION_OPEN : Entry OK + company risk LOW or MEDIUM
    OPPORTUNITY_FOUND --> WATCHING : REJECT

    POSITION_OPEN --> WATCHING : price >= target_price — SELL
    POSITION_OPEN --> WATCHING : price <= stop_loss — SELL
```

### Analysis triggers

| State | Trigger | Agents involved |
|---|---|---|
| `WATCHING` | Every `analysis_interval_hours` (default 4h) or force event | TechnicalAgent (90-day bars) + market context from DB → TradingAgent |
| `OPPORTUNITY_FOUND` | Every poll cycle (default 60s) | TechnicalAgent (5-day 15-min bars) + CompanyAnalysisAgent → TradingAgent |
| `POSITION_OPEN` | Every poll cycle | Price check only (no LLM call) |
| Global | Every `analysis_interval_hours` | MarketContextAgent (general market news → DB) |

---

## System diagram

```mermaid
flowchart TD
    UI["Tkinter Launcher<br/>Monitor ON/OFF · Analizar ahora · Dashboard"]
    PM["PriceMonitor<br/>async loop · 60s poll"]
    TA["TechnicalAgent"]
    MC["MarketContextAgent<br/>every 4h · global"]
    CA["CompanyAnalysisAgent<br/>on OPPORTUNITY_FOUND"]
    TR["TradingAgent<br/>orchestrator"]
    ALP["Alpaca API<br/>paper / live"]
    DB[("SQLite<br/>7 tables")]
    EMAIL["Gmail SMTP<br/>HTML alerts"]
    LOGS["logs/<br/>agent.log · prompts.log"]
    DASH["Streamlit Dashboard"]

    UI -->|start/stop| PM
    PM --> TA
    PM --> MC
    PM --> CA
    TA --> TR
    MC --> TR
    CA --> TR
    TR -->|OPEN_POSITION| ALP
    ALP --> DB
    ALP --> EMAIL
    PM --> DB
    PM --> LOGS
    DB --> DASH
```

---

## Agent diagrams

### TechnicalAnalysisAgent

Analyzes OHLCV price bars to identify trends and entry/exit levels. Called in two modes: medium-term (90 daily bars) for `WATCHING` analysis, and short-term (96 × 15-min bars) for `OPPORTUNITY_FOUND` entry timing.

```mermaid
flowchart TB
    subgraph IN["📥 Input · text"]
        I1["OHLCV bars — Alpaca API"]
        --- I2["Current price"]
    end

    AG["TechnicalAnalysisAgent<br/>model: technical · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["trend: BULLISH · BEARISH · SIDEWAYS"]
        --- O2["signal: BUY · SELL · HOLD"]
        --- O3["target_price"]
        --- O4["stop_loss"]
        --- O5["confidence (0–100)"]
        --- O6["summary"]
    end

    IN --> AG --> OUT
```

---

### MarketContextAgent

Produces a holistic macro view of the global market once every `analysis_interval_hours`. The report is persisted in the `market_context` table and read by all symbols during their `WATCHING` analysis — no redundant API calls.

```mermaid
flowchart TB
    subgraph IN["📥 Input · text"]
        I1["General market news — GNews API<br/>'stock market economy'"]
    end

    AG["MarketContextAgent<br/>model: economics · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["market_sentiment: BULLISH · BEARISH · NEUTRAL"]
        --- O2["risk_appetite: RISK_ON · RISK_OFF · NEUTRAL"]
        --- O3["dominant_themes: list of 2–5 themes"]
        --- O4["macro_risks: list of 1–4 risks"]
        --- O5["confidence (0–100)"]
        --- O6["summary"]
    end

    IN --> AG --> OUT
```

---

### CompanyAnalysisAgent

Fetches company-specific news when a symbol reaches `OPPORTUNITY_FOUND` and assesses the risk of opening a position right now. Result is passed directly to `TradingAgent` as context.

```mermaid
flowchart TB
    subgraph IN["📥 Input · text"]
        I1["Company news — GNews API"]
        --- I2["Symbol"]
    end

    AG["CompanyAnalysisAgent<br/>model: economics · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["sentiment: POSITIVE · NEGATIVE · NEUTRAL"]
        --- O2["key_events: list of 1–4 events"]
        --- O3["entry_risk: LOW · MEDIUM · HIGH"]
        --- O4["confidence (0–100)"]
        --- O5["summary"]
    end

    IN --> AG --> OUT
```

---

### TradingAgent (orchestrator)

Makes the final trading decision based on sub-agent reports. Called twice per symbol cycle: once to detect opportunities (`WATCHING → OPPORTUNITY_FOUND`) and once to decide entry (`OPPORTUNITY_FOUND → POSITION_OPEN`).

```mermaid
flowchart TB
    subgraph IN["📥 Input · JSON + text"]
        I1["TechnicalAgent report"]
        --- I2["MarketContext report or CompanyAnalysis report"]
        --- I3["Current price · max_qty"]
    end

    AG["TradingAgent — orchestrator<br/>model: trader · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["decision: OPEN_POSITION · HOLD · REJECT"]
        --- O2["symbol"]
        --- O3["qty"]
        --- O4["reasoning"]
    end

    IN --> AG --> OUT
```

**Decision rules:**
- `OPEN_POSITION` — technical signal is BUY, market is RISK_ON or NEUTRAL, company entry_risk is LOW or MEDIUM, confidence ≥ 60.
- `HOLD` — signals conflict, confidence is low, or market is RISK_OFF but not severely.
- `REJECT` — signals clearly unfavorable, entry_risk is HIGH, or market is strongly BEARISH.

---

## Module contracts

| Module | Input | Output |
|---|---|---|
| **TechnicalAgent** | OHLCV bars JSON | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **MarketContextAgent** | General news JSON | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| **CompanyAnalysisAgent** | Company news JSON | `{sentiment, key_events, entry_risk, confidence, summary}` |
| **TradingAgent** | Sub-agent reports | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty, reasoning}` |
| **AlpacaClient** | Credentials + params | Prices, orders, assets (async REST) |
| **StorageLayer** | Various | SQLite persistence (7 tables) |
| **TradeAlerter** | Trade details | HTML email via Gmail SMTP |

## Key design constraints

- Each agent is independently replaceable (swap model, planner, or provider in YAML).
- `SimplePlanner` embeds the system prompt in the user message.
- All crypto symbols are filtered out (`asset_class=us_equity`, `exchange != CRYPTO`).
- SQLite deduplication uses `url` as unique key for articles; `symbol` as primary key for `symbol_states`.
- `AgentLogger` writes to `logs/agent.log` and `logs/prompts.log` (rotating, configurable via `LOG_LEVEL`).
