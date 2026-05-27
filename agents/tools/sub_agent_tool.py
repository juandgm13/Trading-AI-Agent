"""Tool: delega tareas a sub-agentes especializados por nombre."""
import json
from agent_framework.tools.base_tool import BaseTool


class RunSubAgentTool(BaseTool):
    name = "run_sub_agent"
    description = (
        "Delegate a specialized analysis task to a named sub-agent. "
        "Each sub-agent has its own tools, LLM, and system prompt tuned for its task. "
        "Available agents: "
        "technical_watching (medium-term daily trend, call with symbol and current price), "
        "technical_entry (short-term 15min entry timing, call when OPPORTUNITY_FOUND), "
        "market_context (global market sentiment from news, call every ~4h), "
        "company_analysis (company-specific news sentiment, call when OPPORTUNITY_FOUND), "
        "trading_opportunity (should we watch this closely? call after technical_watching), "
        "trading_entry (enter position NOW? call after technical_entry + company_analysis)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "enum": [
                    "technical_watching",
                    "technical_entry",
                    "market_context",
                    "company_analysis",
                    "trading_opportunity",
                    "trading_entry",
                ],
                "description": "Name of the sub-agent to delegate to",
            },
            "task": {
                "type": "string",
                "description": (
                    "Instruction for the sub-agent. Be specific: include symbol, "
                    "current price, max position value, and any relevant context. "
                    "Example: 'Analyze AAPL for medium-term trend. Current price: $175.00. "
                    "Max position: $500 USD.'"
                ),
            },
        },
        "required": ["agent_name", "task"],
    }

    def __init__(self, agents: dict):
        """
        Args:
            agents: Mapping of agent_name → Agent instance.
                    Keys must match the enum values in parameters above.
        """
        self._agents = agents

    async def execute(self, agent_name: str, task: str) -> str:
        agent = self._agents.get(agent_name)
        if agent is None:
            available = sorted(self._agents.keys())
            return json.dumps({
                "error": f"Unknown agent '{agent_name}'",
                "available_agents": available,
            })

        try:
            result = await agent.run(task)
            return result
        except Exception as e:
            return json.dumps({
                "error": f"Sub-agent '{agent_name}' failed: {e}",
                "agent_name": agent_name,
                "task": task[:200],
            })
