"""Tools: guarda y recupera informes de sub-agentes y contexto de mercado en la BD."""
import json
from agent_framework.tools.base_tool import BaseTool


# Nombres válidos de agente — allowlist para compatibilidad con el dashboard
_VALID_AGENT_NAMES = frozenset({
    "technical_medium",
    "technical_short",
    "company_analysis",
    "trading_opportunity",
    "trading_entry",
})


class GetLastReportTool(BaseTool):
    name = "get_last_report"
    description = (
        "Read the most recent analysis report saved by a specific sub-agent for a symbol. "
        "Use this to retrieve cached analysis results without re-running the agent. "
        "agent_name must be one of: technical_medium, technical_short, "
        "company_analysis, trading_opportunity, trading_entry."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "agent_name": {
                "type": "string",
                "enum": list(_VALID_AGENT_NAMES),
                "description": (
                    "Name of the sub-agent whose report to retrieve. "
                    "technical_medium=daily trend, technical_short=intraday entry, "
                    "company_analysis=news sentiment, trading_opportunity=opportunity eval, "
                    "trading_entry=entry decision."
                ),
            },
        },
        "required": ["symbol", "agent_name"],
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self, symbol: str, agent_name: str) -> str:
        symbol = symbol.upper().strip()

        if agent_name not in _VALID_AGENT_NAMES:
            return json.dumps({
                "error": f"Unknown agent_name '{agent_name}'. Valid: {sorted(_VALID_AGENT_NAMES)}"
            })

        report = self._storage.get_last_agent_report(symbol, agent_name)
        if report is None:
            return json.dumps({
                "found": False,
                "symbol": symbol,
                "agent_name": agent_name,
                "message": "No report found. Run the sub-agent first.",
            })

        # Try to parse the JSON report content for readability
        report_content = report.get("report", "{}")
        try:
            parsed = json.loads(report_content)
        except (json.JSONDecodeError, TypeError):
            parsed = report_content

        return json.dumps({
            "found": True,
            "symbol": symbol,
            "agent_name": agent_name,
            "created_at": report.get("created_at"),
            "report": parsed,
        })


class SaveAgentReportTool(BaseTool):
    name = "save_agent_report"
    description = (
        "Persist a sub-agent analysis report to the database. "
        "Call this after completing any analysis to preserve results for the dashboard. "
        "agent_name must be one of: technical_medium, technical_short, "
        "company_analysis, trading_opportunity, trading_entry."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "agent_name": {
                "type": "string",
                "enum": list(_VALID_AGENT_NAMES),
                "description": "Name identifier for this report type.",
            },
            "report_json": {
                "type": "string",
                "description": "JSON string containing the analysis report",
            },
        },
        "required": ["symbol", "agent_name", "report_json"],
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self, symbol: str, agent_name: str, report_json: str) -> str:
        symbol = symbol.upper().strip()

        if agent_name not in _VALID_AGENT_NAMES:
            return json.dumps({
                "ok": False,
                "error": f"Unknown agent_name '{agent_name}'. Valid: {sorted(_VALID_AGENT_NAMES)}",
            })

        # Validate that report_json is valid JSON
        try:
            json.loads(report_json)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({
                "ok": False,
                "error": "report_json must be a valid JSON string",
            })

        try:
            self._storage.save_agent_report(symbol, agent_name, report_json)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

        return json.dumps({
            "ok": True,
            "symbol": symbol,
            "agent_name": agent_name,
        })


class SaveMarketContextTool(BaseTool):
    name = "save_market_context"
    description = (
        "Persist a global market context report to the database. "
        "Call this after MarketContextAgent completes its analysis. "
        "The report is used by all symbols in their WATCHING cycle."
    )
    parameters = {
        "type": "object",
        "properties": {
            "report_json": {
                "type": "string",
                "description": "JSON string with the market context analysis",
            },
            "news_count": {
                "type": "integer",
                "description": "Number of news articles that were analysed",
                "default": 0,
            },
        },
        "required": ["report_json"],
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self, report_json: str, news_count: int = 0) -> str:
        try:
            json.loads(report_json)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"ok": False, "error": "report_json must be valid JSON"})

        try:
            self._storage.save_market_context(report_json, news_count)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

        return json.dumps({"ok": True, "news_count": news_count})


class GetMarketContextTool(BaseTool):
    name = "get_market_context"
    description = (
        "Read the most recent global market context report from the database. "
        "Returns the holistic market sentiment, risk appetite, dominant themes and "
        "the timestamp of when it was last updated."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self) -> str:
        ctx = self._storage.get_latest_market_context()
        if ctx is None:
            return json.dumps({
                "found": False,
                "message": "No market context available. Run MarketContextAgent first.",
            })

        report_content = ctx.get("report", "{}")
        try:
            parsed = json.loads(report_content)
        except (json.JSONDecodeError, TypeError):
            parsed = report_content

        return json.dumps({
            "found": True,
            "created_at": ctx.get("created_at"),
            "news_count": ctx.get("news_count", 0),
            "report": parsed,
        })
