"""
Tests unitarios de los nuevos tools de la Fase 2.
No requieren credenciales — todos los clientes externos están mockeados.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


# ── FetchMarketNewsTool ───────────────────────────────────────────────────────

def _make_news_service(fetched=5, classified=3, exc=None):
    svc = MagicMock()
    if exc:
        svc.run_market_ingestion = AsyncMock(side_effect=exc)
        svc.run_symbol_ingestion = AsyncMock(side_effect=exc)
    else:
        svc.run_market_ingestion = AsyncMock(return_value=(fetched, classified))
        svc.run_symbol_ingestion = AsyncMock(return_value=[])
    return svc


def _make_storage_with_articles(articles=None):
    storage = MagicMock()
    storage.get_articles_by_scope = MagicMock(return_value=articles or [])
    storage.get_latest_market_context = MagicMock(return_value=None)
    storage.get_last_agent_report = MagicMock(return_value=None)
    storage.save_agent_report = MagicMock()
    storage.save_market_context = MagicMock()
    storage.get_symbol_state = MagicMock(return_value={
        "symbol": "AAPL", "state": "WATCHING", "last_analysis_at": None,
        "target_price": None, "stop_loss": None, "position_qty": None,
        "pending_order_id": None, "bracket_order_id": None, "position_side": "long",
        "updated_at": "2024-01-01T00:00:00+00:00",
    })
    storage.upsert_symbol_state = MagicMock()
    return storage


SAMPLE_ARTICLES = [
    {
        "title": "Fed raises rates",
        "source": "Reuters",
        "published_at": "2024-01-15T12:00:00Z",
        "impact": "HIGH",
        "scope": "market",
        "article_summary": "Federal Reserve raised rates by 25bps.",
        "description": "Fed description",
        "reasoning": "Macro impact",
        "url": "https://reuters.com/1",
    },
    {
        "title": "Market steady",
        "source": "CNBC",
        "published_at": "2024-01-15T10:00:00Z",
        "impact": "MEDIUM",
        "scope": "market",
        "article_summary": None,
        "description": "Markets held steady today.",
        "reasoning": "Index news",
        "url": "https://cnbc.com/1",
    },
]


@pytest.mark.asyncio
async def test_fetch_market_news_triggers_ingestion_and_returns_articles():
    from agents.tools.news_tool import FetchMarketNewsTool

    svc = _make_news_service()
    storage = _make_storage_with_articles(SAMPLE_ARTICLES)

    tool = FetchMarketNewsTool(svc, storage)
    result = await tool.execute(limit=10, min_impact="MEDIUM")
    data = json.loads(result)

    svc.run_market_ingestion.assert_called_once()
    assert data["articles_returned"] == 2
    assert data["source"] == "market"
    assert data["articles"][0]["title"] == "Fed raises rates"


@pytest.mark.asyncio
async def test_fetch_market_news_uses_article_summary_when_available():
    from agents.tools.news_tool import FetchMarketNewsTool

    svc = _make_news_service()
    storage = _make_storage_with_articles(SAMPLE_ARTICLES)

    tool = FetchMarketNewsTool(svc, storage)
    result = await tool.execute()
    data = json.loads(result)

    # First article has article_summary → should use it
    assert data["articles"][0]["summary"] == "Federal Reserve raised rates by 25bps."
    # Second article has no article_summary → should fall back to description[:200]
    assert "Markets held steady" in data["articles"][1]["summary"]


@pytest.mark.asyncio
async def test_fetch_market_news_ignores_ingestion_error():
    """Si la ingesta falla, debe devolver lo que hay en la BD sin lanzar excepción."""
    from agents.tools.news_tool import FetchMarketNewsTool

    svc = _make_news_service(exc=ConnectionError("network down"))
    storage = _make_storage_with_articles(SAMPLE_ARTICLES)

    tool = FetchMarketNewsTool(svc, storage)
    result = await tool.execute()
    data = json.loads(result)

    # No error key — just returns what's in DB
    assert "error" not in data
    assert data["articles_returned"] == 2


@pytest.mark.asyncio
async def test_fetch_market_news_passes_min_impact_to_storage():
    from agents.tools.news_tool import FetchMarketNewsTool

    svc = _make_news_service()
    storage = _make_storage_with_articles([])

    tool = FetchMarketNewsTool(svc, storage)
    await tool.execute(limit=5, min_impact="HIGH")

    storage.get_articles_by_scope.assert_called_once_with("market", limit=5, min_impact="HIGH")


# ── FetchCompanyNewsTool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_company_news_calls_symbol_ingestion():
    from agents.tools.news_tool import FetchCompanyNewsTool

    svc = MagicMock()
    svc.run_symbol_ingestion = AsyncMock(return_value=SAMPLE_ARTICLES)
    storage = MagicMock()

    tool = FetchCompanyNewsTool(svc, storage)
    result = await tool.execute(symbol="AAPL", exchange="NASDAQ")
    data = json.loads(result)

    svc.run_symbol_ingestion.assert_called_once_with("AAPL", exchange="NASDAQ")
    assert data["symbol"] == "AAPL"
    assert data["articles_returned"] == 2


@pytest.mark.asyncio
async def test_fetch_company_news_uppercases_symbol():
    from agents.tools.news_tool import FetchCompanyNewsTool

    svc = MagicMock()
    svc.run_symbol_ingestion = AsyncMock(return_value=[])
    storage = MagicMock()

    tool = FetchCompanyNewsTool(svc, storage)
    result = await tool.execute(symbol="aapl")
    data = json.loads(result)

    assert data["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_fetch_company_news_falls_back_to_db_on_error():
    from agents.tools.news_tool import FetchCompanyNewsTool

    svc = MagicMock()
    svc.run_symbol_ingestion = AsyncMock(side_effect=ConnectionError("timeout"))
    storage = MagicMock()
    storage.get_articles_by_scope = MagicMock(return_value=SAMPLE_ARTICLES)

    tool = FetchCompanyNewsTool(svc, storage)
    result = await tool.execute(symbol="AAPL")
    data = json.loads(result)

    assert "error" not in data
    assert data["articles_returned"] == 2
    storage.get_articles_by_scope.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_company_news_respects_limit():
    from agents.tools.news_tool import FetchCompanyNewsTool

    many_articles = SAMPLE_ARTICLES * 10  # 20 articles
    svc = MagicMock()
    svc.run_symbol_ingestion = AsyncMock(return_value=many_articles)
    storage = MagicMock()

    tool = FetchCompanyNewsTool(svc, storage)
    result = await tool.execute(symbol="AAPL", limit=3)
    data = json.loads(result)

    assert data["articles_returned"] == 3


# ── GetSymbolStateTool ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_symbol_state_returns_state_from_storage():
    from agents.tools.symbol_state_tool import GetSymbolStateTool

    storage = MagicMock()
    storage.get_symbol_state = MagicMock(return_value={
        "symbol": "AAPL",
        "state": "OPPORTUNITY_FOUND",
        "target_price": 180.0,
        "stop_loss": 170.0,
    })

    tool = GetSymbolStateTool(storage)
    result = await tool.execute(symbol="AAPL")
    data = json.loads(result)

    assert data["symbol"] == "AAPL"
    assert data["state"] == "OPPORTUNITY_FOUND"
    assert data["target_price"] == 180.0


@pytest.mark.asyncio
async def test_get_symbol_state_uppercases_symbol():
    from agents.tools.symbol_state_tool import GetSymbolStateTool

    storage = MagicMock()
    storage.get_symbol_state = MagicMock(return_value={"symbol": "AAPL", "state": "WATCHING"})

    tool = GetSymbolStateTool(storage)
    await tool.execute(symbol="aapl")
    storage.get_symbol_state.assert_called_once_with("AAPL")


# ── UpdateSymbolStateTool ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_symbol_state_calls_storage_upsert():
    from agents.tools.symbol_state_tool import UpdateSymbolStateTool

    storage = MagicMock()
    storage.upsert_symbol_state = MagicMock()

    tool = UpdateSymbolStateTool(storage)
    result = await tool.execute(
        symbol="AAPL",
        state="OPPORTUNITY_FOUND",
        analysis_summary="Bullish breakout detected",
    )
    data = json.loads(result)

    assert data["ok"] is True
    assert data["symbol"] == "AAPL"
    assert data["new_state"] == "OPPORTUNITY_FOUND"
    storage.upsert_symbol_state.assert_called_once()


@pytest.mark.asyncio
async def test_update_symbol_state_only_passes_provided_fields():
    from agents.tools.symbol_state_tool import UpdateSymbolStateTool

    storage = MagicMock()
    storage.upsert_symbol_state = MagicMock()

    tool = UpdateSymbolStateTool(storage)
    await tool.execute(symbol="AAPL", state="WATCHING")

    call_kwargs = storage.upsert_symbol_state.call_args[1]
    # state and last_analysis_at should be set; nothing else
    assert "state" in call_kwargs
    assert "target_price" not in call_kwargs
    assert "stop_loss" not in call_kwargs


@pytest.mark.asyncio
async def test_update_symbol_state_returns_error_on_exception():
    from agents.tools.symbol_state_tool import UpdateSymbolStateTool

    storage = MagicMock()
    storage.upsert_symbol_state = MagicMock(side_effect=Exception("DB error"))

    tool = UpdateSymbolStateTool(storage)
    result = await tool.execute(symbol="AAPL", state="WATCHING")
    data = json.loads(result)

    assert data["ok"] is False
    assert "DB error" in data["error"]


@pytest.mark.asyncio
async def test_update_symbol_state_auto_sets_last_analysis_at_when_watching():
    """Transición a WATCHING debe auto-poblaar last_analysis_at."""
    from agents.tools.symbol_state_tool import UpdateSymbolStateTool

    storage = MagicMock()
    storage.upsert_symbol_state = MagicMock()

    tool = UpdateSymbolStateTool(storage)
    await tool.execute(symbol="AAPL", state="WATCHING")

    call_kwargs = storage.upsert_symbol_state.call_args[1]
    assert "last_analysis_at" in call_kwargs
    assert call_kwargs["last_analysis_at"] is not None


# ── GetLastReportTool ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_last_report_returns_parsed_report():
    from agents.tools.report_tool import GetLastReportTool

    report_data = {"trend": "BULLISH", "signal": "BUY", "confidence": 80}
    storage = MagicMock()
    storage.get_last_agent_report = MagicMock(return_value={
        "symbol": "AAPL",
        "agent_name": "technical_medium",
        "report": json.dumps(report_data),
        "created_at": "2024-01-15T12:00:00Z",
    })

    tool = GetLastReportTool(storage)
    result = await tool.execute(symbol="AAPL", agent_name="technical_medium")
    data = json.loads(result)

    assert data["found"] is True
    assert data["report"]["trend"] == "BULLISH"
    assert data["report"]["confidence"] == 80


@pytest.mark.asyncio
async def test_get_last_report_returns_not_found_when_none():
    from agents.tools.report_tool import GetLastReportTool

    storage = MagicMock()
    storage.get_last_agent_report = MagicMock(return_value=None)

    tool = GetLastReportTool(storage)
    result = await tool.execute(symbol="AAPL", agent_name="technical_short")
    data = json.loads(result)

    assert data["found"] is False
    assert "message" in data


@pytest.mark.asyncio
async def test_get_last_report_rejects_invalid_agent_name():
    from agents.tools.report_tool import GetLastReportTool

    storage = MagicMock()
    tool = GetLastReportTool(storage)
    result = await tool.execute(symbol="AAPL", agent_name="unknown_agent")
    data = json.loads(result)

    assert "error" in data
    storage.get_last_agent_report.assert_not_called()


# ── SaveAgentReportTool ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_agent_report_persists_to_storage():
    from agents.tools.report_tool import SaveAgentReportTool

    storage = MagicMock()
    storage.save_agent_report = MagicMock()

    tool = SaveAgentReportTool(storage)
    report = json.dumps({"trend": "BULLISH", "confidence": 75})
    result = await tool.execute(symbol="AAPL", agent_name="technical_medium", report_json=report)
    data = json.loads(result)

    assert data["ok"] is True
    storage.save_agent_report.assert_called_once_with("AAPL", "technical_medium", report)


@pytest.mark.asyncio
async def test_save_agent_report_rejects_invalid_agent_name():
    from agents.tools.report_tool import SaveAgentReportTool

    storage = MagicMock()
    tool = SaveAgentReportTool(storage)
    result = await tool.execute(
        symbol="AAPL", agent_name="bad_name", report_json='{"x": 1}'
    )
    data = json.loads(result)

    assert data["ok"] is False
    assert "error" in data
    storage.save_agent_report.assert_not_called()


@pytest.mark.asyncio
async def test_save_agent_report_rejects_invalid_json():
    from agents.tools.report_tool import SaveAgentReportTool

    storage = MagicMock()
    tool = SaveAgentReportTool(storage)
    result = await tool.execute(
        symbol="AAPL", agent_name="technical_medium", report_json="not valid json"
    )
    data = json.loads(result)

    assert data["ok"] is False
    storage.save_agent_report.assert_not_called()


@pytest.mark.asyncio
async def test_save_agent_report_uppercases_symbol():
    from agents.tools.report_tool import SaveAgentReportTool

    storage = MagicMock()
    storage.save_agent_report = MagicMock()

    tool = SaveAgentReportTool(storage)
    await tool.execute(symbol="aapl", agent_name="company_analysis", report_json='{"x": 1}')
    storage.save_agent_report.assert_called_once_with("AAPL", "company_analysis", '{"x": 1}')


# ── SaveMarketContextTool ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_market_context_persists_to_storage():
    from agents.tools.report_tool import SaveMarketContextTool

    storage = MagicMock()
    storage.save_market_context = MagicMock()

    tool = SaveMarketContextTool(storage)
    report = json.dumps({"market_sentiment": "BULLISH"})
    result = await tool.execute(report_json=report, news_count=10)
    data = json.loads(result)

    assert data["ok"] is True
    assert data["news_count"] == 10
    storage.save_market_context.assert_called_once_with(report, 10)


@pytest.mark.asyncio
async def test_save_market_context_rejects_invalid_json():
    from agents.tools.report_tool import SaveMarketContextTool

    storage = MagicMock()
    tool = SaveMarketContextTool(storage)
    result = await tool.execute(report_json="invalid")
    data = json.loads(result)

    assert data["ok"] is False
    storage.save_market_context.assert_not_called()


# ── GetMarketContextTool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_market_context_returns_parsed_report():
    from agents.tools.report_tool import GetMarketContextTool

    ctx_data = {"market_sentiment": "BEARISH", "confidence": 70}
    storage = MagicMock()
    storage.get_latest_market_context = MagicMock(return_value={
        "report": json.dumps(ctx_data),
        "news_count": 8,
        "created_at": "2024-01-15T08:00:00Z",
    })

    tool = GetMarketContextTool(storage)
    result = await tool.execute()
    data = json.loads(result)

    assert data["found"] is True
    assert data["report"]["market_sentiment"] == "BEARISH"
    assert data["news_count"] == 8


@pytest.mark.asyncio
async def test_get_market_context_returns_not_found_when_none():
    from agents.tools.report_tool import GetMarketContextTool

    storage = MagicMock()
    storage.get_latest_market_context = MagicMock(return_value=None)

    tool = GetMarketContextTool(storage)
    result = await tool.execute()
    data = json.loads(result)

    assert data["found"] is False


# ── GetMarketClockTool ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_market_clock_returns_clock_data():
    from agents.tools.market_clock_tool import GetMarketClockTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": "2024-01-16T14:30:00Z",
        "next_close": "2024-01-15T21:00:00Z",
        "timestamp": "2024-01-15T16:00:00Z",
    })

    tool = GetMarketClockTool(mock_alpaca)
    result = await tool.execute()
    data = json.loads(result)

    assert data["is_open"] is True
    assert "next_open" in data
    assert "next_close" in data


@pytest.mark.asyncio
async def test_get_market_clock_returns_error_on_exception():
    from agents.tools.market_clock_tool import GetMarketClockTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_clock = AsyncMock(side_effect=ConnectionError("API unreachable"))

    tool = GetMarketClockTool(mock_alpaca)
    result = await tool.execute()
    data = json.loads(result)

    assert "error" in data


# ── GetOrderStatusTool ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_order_status_returns_filled_order():
    from agents.tools.order_status_tool import GetOrderStatusTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "order-123",
        "status": "filled",
        "symbol": "AAPL",
        "side": "buy",
        "filled_avg_price": 175.50,
        "filled_qty": 2.0,
        "commission": 0.0,
        "legs": [],
    })

    tool = GetOrderStatusTool(mock_alpaca)
    result = await tool.execute(order_id="order-123")
    data = json.loads(result)

    assert data["found"] is True
    assert data["status"] == "filled"
    assert data["filled_avg_price"] == 175.50


@pytest.mark.asyncio
async def test_get_order_status_returns_not_found_when_none():
    from agents.tools.order_status_tool import GetOrderStatusTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_order = AsyncMock(return_value=None)

    tool = GetOrderStatusTool(mock_alpaca)
    result = await tool.execute(order_id="nonexistent-id")
    data = json.loads(result)

    assert data["found"] is False


@pytest.mark.asyncio
async def test_get_order_status_returns_error_on_exception():
    from agents.tools.order_status_tool import GetOrderStatusTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_order = AsyncMock(side_effect=RuntimeError("API timeout"))

    tool = GetOrderStatusTool(mock_alpaca)
    result = await tool.execute(order_id="order-abc")
    data = json.loads(result)

    assert "error" in data


@pytest.mark.asyncio
async def test_get_order_status_includes_bracket_legs():
    from agents.tools.order_status_tool import GetOrderStatusTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "bracket-order",
        "status": "partially_filled",
        "symbol": "MSFT",
        "side": "buy",
        "filled_avg_price": None,
        "filled_qty": 0.0,
        "commission": 0.0,
        "legs": [
            {"id": "leg-tp", "status": "held", "type": "limit", "side": "sell"},
            {"id": "leg-sl", "status": "held", "type": "stop", "side": "sell"},
        ],
    })

    tool = GetOrderStatusTool(mock_alpaca)
    result = await tool.execute(order_id="bracket-order")
    data = json.loads(result)

    assert data["found"] is True
    assert len(data["legs"]) == 2


# ── RunSubAgentTool ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_sub_agent_dispatches_to_correct_agent():
    from agents.tools.sub_agent_tool import RunSubAgentTool

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value='{"trend": "BULLISH"}')

    tool = RunSubAgentTool({"technical_watching": mock_agent})
    result = await tool.execute(
        agent_name="technical_watching",
        task="Analyze AAPL for medium-term trend."
    )

    mock_agent.run.assert_called_once_with("Analyze AAPL for medium-term trend.")
    assert result == '{"trend": "BULLISH"}'


@pytest.mark.asyncio
async def test_run_sub_agent_returns_error_for_unknown_agent():
    from agents.tools.sub_agent_tool import RunSubAgentTool

    tool = RunSubAgentTool({"technical_watching": MagicMock()})
    result = await tool.execute(agent_name="nonexistent", task="some task")
    data = json.loads(result)

    assert "error" in data
    assert "nonexistent" in data["error"]


@pytest.mark.asyncio
async def test_run_sub_agent_returns_error_when_agent_raises():
    from agents.tools.sub_agent_tool import RunSubAgentTool

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    tool = RunSubAgentTool({"market_context": mock_agent})
    result = await tool.execute(agent_name="market_context", task="Update context")
    data = json.loads(result)

    assert "error" in data
    assert "market_context" in data["error"]


@pytest.mark.asyncio
async def test_run_sub_agent_lists_available_agents_on_error():
    from agents.tools.sub_agent_tool import RunSubAgentTool

    tool = RunSubAgentTool({"technical_watching": MagicMock(), "company_analysis": MagicMock()})
    result = await tool.execute(agent_name="bad_name", task="task")
    data = json.loads(result)

    assert "available_agents" in data
    assert "technical_watching" in data["available_agents"]
    assert "company_analysis" in data["available_agents"]
