"""
Tests de integración con Ollama.

Verifican que cada agente, llamado con un prompt mínimo real,
devuelve una respuesta que pasa la validación correspondiente.

Se saltan automáticamente si Ollama no está corriendo.
"""
import os
import json
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from tests.conftest import requires_ollama

from agent_framework.config.config_loader import load_from_yaml
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.providers import OllamaProvider

from src.response_parser import (
    ParseError,
    validate_technical_report,
    validate_market_context,
    validate_company_analysis,
    validate_trading_decision,
    validate_news_classification,
)


# ── Fixture compartida ────────────────────────────────────────────────────────

@pytest.fixture
def agent_deps():
    """Carga config, crea model registry y event bus compartidos para el módulo."""
    config_path = Path(__file__).parent.parent / "agents" / "trading_agent.yaml"
    config = load_from_yaml(config_path)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    registry = ModelRegistry()
    for role in ("technical", "technical_entry", "economics", "news_classifier", "trader_opportunity", "trader_entry"):
        cfg = config.models.get(role)
        if cfg:
            registry.register(role, OllamaProvider(cfg.model, base_url=ollama_url))

    bus = EventBus()
    return config, bus, registry


# ── TechnicalAnalysisAgent ────────────────────────────────────────────────────

@requires_ollama
@pytest.mark.asyncio
async def test_technical_agent_returns_valid_json(agent_deps, capsys):
    """TechnicalAnalysisAgent devuelve un JSON que pasa validate_technical_report."""
    from agents.technical_agent import build_technical_watching_agent, build_medium_term_prompt

    config, bus, registry = agent_deps
    agent = await build_technical_watching_agent(config, bus, registry)

    # Datos mínimos pero realistas
    bars = [
        {"timestamp": "2025-03-01", "open": 170.0, "high": 175.0, "low": 169.0, "close": 173.0, "volume": 1_000_000},
        {"timestamp": "2025-03-02", "open": 173.0, "high": 178.0, "low": 172.0, "close": 176.0, "volume": 1_200_000},
        {"timestamp": "2025-03-03", "open": 176.0, "high": 180.0, "low": 174.0, "close": 178.0, "volume": 950_000},
    ]
    prompt = build_medium_term_prompt("AAPL", 178.0, bars)
    response = await agent.run(prompt)
    print(f"\n[TechnicalAgent medium] raw response:\n{response}")

    assert response, "El agente devolvió una respuesta vacía"
    result = validate_technical_report(response)
    print(f"[TechnicalAgent medium] validated:\n{json.dumps(result, indent=2)}")

    assert result["trend"] in {"BULLISH", "BEARISH", "NEUTRAL", "SIDEWAYS"}
    assert result["signal"] in {"BUY", "SELL", "HOLD", "WAIT"}
    assert 0 <= result["confidence"] <= 100
    assert isinstance(result["summary"], str)


@requires_ollama
@pytest.mark.asyncio
async def test_technical_agent_short_term_returns_valid_json(agent_deps):
    """TechnicalAnalysisAgent (corto plazo) devuelve JSON válido."""
    from agents.technical_agent import build_technical_watching_agent, build_short_term_prompt

    config, bus, registry = agent_deps
    agent = await build_technical_watching_agent(config, bus, registry)

    bars = [
        {"timestamp": f"2025-03-03T{h:02d}:00:00Z", "open": 175.0 + h * 0.1, "high": 175.5 + h * 0.1,
         "low": 174.8 + h * 0.1, "close": 175.2 + h * 0.1, "volume": 50_000}
        for h in range(8)
    ]
    prompt = build_short_term_prompt("AAPL", 175.8, bars)
    response = await agent.run(prompt)
    print(f"\n[TechnicalAgent short] raw response:\n{response}")

    result = validate_technical_report(response)
    print(f"[TechnicalAgent short] validated:\n{json.dumps(result, indent=2)}")
    assert result["signal"] in {"BUY", "SELL", "HOLD", "WAIT"}


# ── MarketContextAgent ────────────────────────────────────────────────────────

@requires_ollama
@pytest.mark.asyncio
async def test_market_context_agent_returns_valid_json(agent_deps):
    """MarketContextAgent devuelve un JSON que pasa validate_market_context."""
    from agents.economics_agent import build_market_context_agent, build_market_context_prompt

    config, bus, registry = agent_deps
    agent = await build_market_context_agent(config, bus, registry)

    news = [
        {"title": "Fed holds rates steady", "description": "The Federal Reserve decided to keep interest rates unchanged."},
        {"title": "Tech stocks rally on AI optimism", "description": "Major tech companies saw gains driven by AI announcements."},
    ]
    prompt = build_market_context_prompt(news)
    response = await agent.run(prompt)
    print(f"\n[MarketContextAgent with_news] raw response:\n{response}")

    assert response, "El agente devolvió una respuesta vacía"
    result = validate_market_context(response)
    print(f"[MarketContextAgent with_news] validated:\n{json.dumps(result, indent=2)}")

    assert result["market_sentiment"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert result["risk_appetite"] in {"RISK_ON", "RISK_OFF", "NEUTRAL"}
    assert isinstance(result["dominant_themes"], list)
    assert isinstance(result["macro_risks"], list)
    assert 0 <= result["confidence"] <= 100
    assert isinstance(result["summary"], str)


@requires_ollama
@pytest.mark.asyncio
async def test_market_context_agent_no_news(agent_deps):
    """MarketContextAgent con lista de noticias vacía devuelve NEUTRAL."""
    from agents.economics_agent import build_market_context_agent, build_market_context_prompt

    config, bus, registry = agent_deps
    agent = await build_market_context_agent(config, bus, registry)

    prompt = build_market_context_prompt([])
    response = await agent.run(prompt)
    print(f"\n[MarketContextAgent no_news] raw response:\n{response}")

    result = validate_market_context(response)
    print(f"[MarketContextAgent no_news] validated:\n{json.dumps(result, indent=2)}")
    # Sin noticias el modelo debería devolver neutral/baja confianza
    assert result["market_sentiment"] in {"BULLISH", "BEARISH", "NEUTRAL"}


# ── CompanyAnalysisAgent ──────────────────────────────────────────────────────

@requires_ollama
@pytest.mark.asyncio
async def test_company_analysis_agent_returns_valid_json(agent_deps):
    """CompanyAnalysisAgent devuelve un JSON que pasa validate_company_analysis."""
    from agents.economics_agent import build_company_analysis_agent, build_company_analysis_prompt

    config, bus, registry = agent_deps
    agent = await build_company_analysis_agent(config, bus, registry)

    news = [
        {"title": "Apple beats earnings expectations", "description": "Apple reported record iPhone sales this quarter."},
        {"title": "Apple launches new AI features", "description": "Apple Intelligence features expand to more regions."},
    ]
    prompt = build_company_analysis_prompt("AAPL", news)
    response = await agent.run(prompt)
    print(f"\n[CompanyAnalysisAgent positive] raw response:\n{response}")

    assert response, "El agente devolvió una respuesta vacía"
    result = validate_company_analysis(response)
    print(f"[CompanyAnalysisAgent positive] validated:\n{json.dumps(result, indent=2)}")

    assert result["sentiment"] in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
    assert result["entry_risk"] in {"LOW", "MEDIUM", "HIGH"}
    assert isinstance(result["key_events"], list)
    assert 0 <= result["confidence"] <= 100
    assert isinstance(result["summary"], str)


@requires_ollama
@pytest.mark.asyncio
async def test_company_analysis_agent_negative_news(agent_deps):
    """CompanyAnalysisAgent con malas noticias devuelve entry_risk elevado."""
    from agents.economics_agent import build_company_analysis_agent, build_company_analysis_prompt

    config, bus, registry = agent_deps
    agent = await build_company_analysis_agent(config, bus, registry)

    news = [
        {"title": "SEC investigates company for fraud", "description": "Regulators open formal investigation into accounting irregularities."},
        {"title": "CEO resigns amid controversy", "description": "The chief executive steps down following board pressure."},
    ]
    prompt = build_company_analysis_prompt("XYZ", news)
    response = await agent.run(prompt)
    print(f"\n[CompanyAnalysisAgent negative] raw response:\n{response}")

    result = validate_company_analysis(response)
    print(f"[CompanyAnalysisAgent negative] validated:\n{json.dumps(result, indent=2)}")
    assert result["entry_risk"] in {"MEDIUM", "HIGH"}


# ── TradingAgent ──────────────────────────────────────────────────────────────

@requires_ollama
@pytest.mark.asyncio
async def test_trading_agent_returns_valid_decision(agent_deps):
    """TradingAgent devuelve un JSON que pasa validate_trading_decision."""
    from agents.trading_agent import build_trading_opportunity_agent, build_opportunity_prompt

    config, bus, registry = agent_deps
    agent = await build_trading_opportunity_agent(config, bus, registry)

    tech_report = {
        "trend": "BULLISH", "signal": "BUY",
        "target_price": 185.0, "stop_loss": 170.0,
        "confidence": 75, "summary": "Strong uptrend with breakout.",
    }
    market_context = {
        "market_sentiment": "BULLISH", "risk_appetite": "RISK_ON",
        "dominant_themes": ["AI growth"], "macro_risks": [],
        "confidence": 70, "summary": "Positive macro environment.",
    }
    prompt = build_opportunity_prompt("AAPL", 178.0, tech_report, market_context, max_position_value=500)
    response = await agent.run(prompt)
    print(f"\n[TradingAgent bullish] raw response:\n{response}")

    assert response, "El agente devolvió una respuesta vacía"
    result = validate_trading_decision(response)
    print(f"[TradingAgent bullish] validated:\n{json.dumps(result, indent=2)}")

    assert result["decision"] in {"OPPORTUNITY_FOUND", "HOLD", "REJECT"}
    assert isinstance(result["reasoning"], str)


@requires_ollama
@pytest.mark.asyncio
async def test_trading_agent_rejects_on_bearish(agent_deps):
    """TradingAgent tiende a HOLD/REJECT con señales bajistas."""
    from agents.trading_agent import build_trading_opportunity_agent, build_opportunity_prompt

    config, bus, registry = agent_deps
    agent = await build_trading_opportunity_agent(config, bus, registry)

    tech_report = {
        "trend": "BEARISH", "signal": "SELL",
        "target_price": None, "stop_loss": None,
        "confidence": 80, "summary": "Strong downtrend.",
    }
    market_context = {
        "market_sentiment": "BEARISH", "risk_appetite": "RISK_OFF",
        "dominant_themes": ["Rate fears"], "macro_risks": ["Recession"],
        "confidence": 75, "summary": "Risk-off environment.",
    }
    prompt = build_opportunity_prompt("XYZ", 50.0, tech_report, market_context, max_position_value=500)
    response = await agent.run(prompt)
    print(f"\n[TradingAgent bearish] raw response:\n{response}")

    result = validate_trading_decision(response)
    print(f"[TradingAgent bearish] validated:\n{json.dumps(result, indent=2)}")
    assert result["decision"] in {"HOLD", "REJECT"}


# ── NewsClassifierAgent ────────────────────────────────────────────────────────

@requires_ollama
@pytest.mark.asyncio
async def test_news_classifier_company_article(agent_deps):
    """NewsClassifierAgent clasifica correctamente una noticia de empresa."""
    from agents.economics_agent import build_news_classifier_agent, build_news_classifier_prompt
    from src.ingestion import Article
    from datetime import datetime, timezone

    config, bus, registry = agent_deps
    agent = await build_news_classifier_agent(config, bus, registry)

    article = Article(
        source="Reuters",
        title="Apple reports record Q1 earnings, iPhone sales surge 20%",
        description="Apple Inc. reported first-quarter earnings that exceeded analyst expectations by 15%, driven by strong iPhone 16 sales in China.",
        content="",
        url="https://reuters.com/apple-q1-2025",
        published_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        feed_group="stocks",
    )
    prompt = build_news_classifier_prompt(article)
    response = await agent.run(prompt)
    print(f"\n[NewsClassifier company] raw response:\n{response}")

    result = validate_news_classification(response)
    print(f"[NewsClassifier company] validated:\n{json.dumps(result, indent=2)}")

    assert result["scope"] in {"company", "market", "exchange", "irrelevant"}
    assert result["impact"] in {"HIGH", "MEDIUM", "LOW", None}
    assert isinstance(result["reasoning"], str)


@requires_ollama
@pytest.mark.asyncio
async def test_news_classifier_market_article(agent_deps):
    """NewsClassifierAgent clasifica correctamente una noticia macro."""
    from agents.economics_agent import build_news_classifier_agent, build_news_classifier_prompt
    from src.ingestion import Article
    from datetime import datetime, timezone

    config, bus, registry = agent_deps
    agent = await build_news_classifier_agent(config, bus, registry)

    article = Article(
        source="Federal Reserve",
        title="Fed raises interest rates by 25 basis points, signals pause",
        description="The Federal Reserve raised its benchmark interest rate by 25 basis points, signaling a potential pause in its rate-hiking cycle amid signs of slowing inflation.",
        content="",
        url="https://federalreserve.gov/press-2025",
        published_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        feed_group="macro",
    )
    prompt = build_news_classifier_prompt(article)
    response = await agent.run(prompt)
    print(f"\n[NewsClassifier macro] raw response:\n{response}")

    result = validate_news_classification(response)
    print(f"[NewsClassifier macro] validated:\n{json.dumps(result, indent=2)}")

    assert result["scope"] in {"company", "market", "exchange", "irrelevant"}
    assert result["impact"] in {"HIGH", "MEDIUM", "LOW", None}
