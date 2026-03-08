import json
import os
import re
from dataclasses import dataclass

import ollama

DEFAULT_MODEL_NAME = "qwen2.5:7b"

VALID_IMPACT_LEVELS = {"LOW", "MEDIUM", "HIGH"}
VALID_IMPACT_TYPES = {"POSITIVE", "NEGATIVE", "NEUTRAL"}

SYSTEM_PROMPT = (
    "You are a financial news analyst. "
    "Analyze the article and return ONLY a valid JSON object, no explanation.\n\n"
    "Rules for the 'company' field:\n"
    "- Use the company name when the news is primarily about a specific company.\n"
    "- Use 'MARKET' when the news affects the general market (e.g. Fed rate decisions, "
    "inflation data, GDP reports, stock index movements, global economic events).\n"
    "- Use 'SECTOR:<name>' when the news affects an entire sector but not a specific company "
    "(e.g. 'SECTOR:ENERGY', 'SECTOR:TECH', 'SECTOR:BANKING').\n"
    "- Use 'Unknown' only when the affected entity truly cannot be determined.\n\n"
    "Rules for the 'ticker' field:\n"
    "- Use the stock exchange ticker when company is a specific company.\n"
    "- Use 'N/A' when company is MARKET, SECTOR:*, or Unknown.\n\n"
    "Schema:\n"
    '{\n'
    '  "company": "<company name | MARKET | SECTOR:<name> | Unknown>",\n'
    '  "ticker": "<ticker or \'N/A\'>",\n'
    '  "impact_level": "<LOW|MEDIUM|HIGH>",\n'
    '  "impact_type": "<POSITIVE|NEGATIVE|NEUTRAL>",\n'
    '  "confidence": <integer 0-100>,\n'
    '  "reason": "<one sentence explaining the impact assessment>"\n'
    '}'
)

USER_TEMPLATE = "Title: {title}\nContent: {content}"


@dataclass
class LLMOutput:
    company: str
    ticker: str
    impact_level: str
    impact_type: str
    confidence: int
    reason: str


class LLMAnalysisModule:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)

        try:
            ollama.list()
        except Exception as e:
            raise RuntimeError(
                "[llm] Ollama server is not running. Start it with: ollama serve"
            ) from e

        print(f"[llm] Using Ollama model: {self.model_name}")

    def analyze(self, title: str, content: str) -> LLMOutput:
        response = ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(title=title, content=content)},
            ],
            format="json",
        )
        raw = response["message"]["content"].strip()
        return self._parse_output(raw)

    def _parse_output(self, raw: str) -> LLMOutput:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"[llm] No JSON found in model output:\n{raw}")

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"[llm] Invalid JSON in model output: {e}\nRaw output:\n{raw}") from e

        impact_level = str(data.get("impact_level", "")).upper()
        impact_type = str(data.get("impact_type", "")).upper()

        if impact_level not in VALID_IMPACT_LEVELS:
            raise ValueError(f"[llm] Invalid impact_level '{impact_level}'. Raw output:\n{raw}")
        if impact_type not in VALID_IMPACT_TYPES:
            raise ValueError(f"[llm] Invalid impact_type '{impact_type}'. Raw output:\n{raw}")

        return LLMOutput(
            company=str(data.get("company", "Unknown")),
            ticker=str(data.get("ticker", "N/A")),
            impact_level=impact_level,
            impact_type=impact_type,
            confidence=int(data.get("confidence", 0)),
            reason=str(data.get("reason", "")),
        )
