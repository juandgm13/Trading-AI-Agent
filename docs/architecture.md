# Architecture

## Multi-agent state machine

Each symbol has an independent state machine managed by `PriceMonitor`:

```
Global market context (every analysis_interval_hours, default: 4h)
  MarketContextAgent fetches general market news → holistic sentiment report (persisted in DB)

WATCHING ──────────────────────────────────────────────┐
    │  (every analysis_interval_hours, default: 4h)    │
    │  TechnicalAgent (90-day daily bars)               │
    │  + global market context (read from DB)           │
    │  TradingAgent evaluates → opportunity?            │
    ▼                                                   │
OPPORTUNITY_FOUND                                       │
    │  (every poll cycle, default: 60s)                │
    │  TechnicalAgent (5-day 15-min bars)               │
    │  CompanyAnalysisAgent (company-specific news)     │
    │  TradingAgent decides → open position?            │
    ▼                                    │ REJECT        │
POSITION_OPEN ◄──────────────────────────┘              │
    │  (every poll cycle)                               │
    │  price >= target_price → SELL → WATCHING ─────────┘
    │  price <= stop_loss    → SELL → WATCHING ─────────┘
```

## Agent roles

All agents use the `simple` planner (text-in / JSON-out), no tool loops.

| Agent | File | Model role | Input | Output |
|---|---|---|---|---|
| `TechnicalAnalysisAgent` | `agents/technical_agent.py` | `technical` | OHLCV bars (pre-fetched) | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `MarketContextAgent` | `agents/economics_agent.py` | `economics` | General market news (GNews) | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| `CompanyAnalysisAgent` | `agents/economics_agent.py` | `economics` | Company-specific news (GNews) | `{sentiment, key_events, entry_risk, confidence, summary}` |
| `TradingAgent` (orchestrator) | `agents/trading_agent.py` | `trader` | Sub-agent reports | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty, reasoning}` |

### News analysis split

- **MarketContextAgent** — runs every 4h, fetches general market news, produces a holistic macro view shared across all symbols. Avoids redundant API calls.
- **CompanyAnalysisAgent** — runs on demand when a symbol reaches `OPPORTUNITY_FOUND`, fetches company-specific news and assesses entry risk (`LOW / MEDIUM / HIGH`).

## System diagram

```
┌──────────────────────────────────────────────────────┐
│                   Tkinter Launcher                    │
│   [Monitor ON/OFF]  [Analizar ahora]  [Dashboard]    │
└─────────────────────────┬────────────────────────────┘
                          │
                ┌─────────▼──────────┐
                │   PriceMonitor     │
                │  async · 60s poll  │
                └──┬──────┬──────┬───┘
                   │      │      │
             WATCHING  OPPORT. POSITION
               ▼  4h   ▼ 60s    ▼ 60s
        ┌──────────────────────────────┐
        │      Sub-agent pipeline      │
        │  TechnicalAgent              │
        │  MarketContextAgent (4h)     │
        │  CompanyAnalysisAgent (entry)│
        │  TradingAgent (orchestrator) │
        └──────────────┬───────────────┘
                       │ OPEN_POSITION
              ┌────────▼────────┐
              │   Alpaca API    │
              │ (paper / live)  │
              └────────┬────────┘
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    SQLite DB      Gmail SMTP    logs/
    (7 tables)    (HTML alerts)  agent.log
                                prompts.log
```

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
