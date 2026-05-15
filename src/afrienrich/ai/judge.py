"""AI judge via OpenRouter. Default: Gemini Flash 2.0. High-quality: DeepSeek V3."""

from __future__ import annotations

import json
import time
import uuid
from typing import TypeVar

import structlog
from pydantic import BaseModel

from ..models import AuditEntry
from .client import get_async_client
from .prompts import (
    SYSTEM_PROMPT,
    build_disambiguate_prompt,
    build_extract_contact_prompt,
    build_translate_name_prompt,
)

log = structlog.get_logger()

# google/gemini-2.0-flash-001: $0.10/$0.40 per 1M tokens — fast, multilingual, great for JSON
_DEFAULT_MODEL = "google/gemini-2.0-flash-001"
# deepseek/deepseek-chat-v3-0324: $0.28/$1.10 per 1M — top-tier reasoning, structured output
_HIGH_QUALITY_MODEL = "deepseek/deepseek-chat-v3-0324"

# Cost per 1M tokens (input, output) in USD
_COST_PER_1M: dict[str, tuple[float, float]] = {
    _DEFAULT_MODEL: (0.10, 0.40),
    _HIGH_QUALITY_MODEL: (0.28, 1.10),
}

MAX_COST_PER_ROW = 0.10
MAX_COST_PER_RUN = 20.0


class DisambiguateResult(BaseModel):
    choice: str  # "A", "B", or "none"
    confidence: int
    reasoning: str


class ExtractContactResult(BaseModel):
    phones: list[str] = []
    emails: list[str] = []
    website: str | None = None
    address: str | None = None
    reasoning: str = ""


class TranslateNameResult(BaseModel):
    translated_name: str
    transliteration: str = ""
    confidence: int


class Judge:
    def __init__(self, high_quality: bool = False) -> None:
        self.model = _HIGH_QUALITY_MODEL if high_quality else _DEFAULT_MODEL
        self._run_cost: float = 0.0

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_rate, output_rate = _COST_PER_1M.get(self.model, (1.0, 4.0))
        return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate

    async def _call(
        self,
        user_prompt: str,
        row_cost_so_far: float,
        company_name: str,
        task: str,
    ) -> tuple[str, AuditEntry, float]:
        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()

        if row_cost_so_far >= MAX_COST_PER_ROW:
            log.warning("ai_judge_row_cap_reached", company=company_name, task=task)
            return "", _skip_audit(audit_id, company_name, task, "row_cap"), 0.0

        if self._run_cost >= MAX_COST_PER_RUN:
            log.warning("ai_judge_run_cap_reached", company=company_name, task=task)
            return "", _skip_audit(audit_id, company_name, task, "run_cap"), 0.0

        try:
            async with get_async_client() as client:
                resp = await client.post(
                    "/chat/completions",
                    json={
                        "model": self.model,
                        "max_tokens": 512,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            input_tok = int(usage.get("prompt_tokens", 0))
            output_tok = int(usage.get("completion_tokens", 0))
            cost = self._estimate_cost(input_tok, output_tok)
            self._run_cost += cost
            latency = int((time.monotonic() - t0) * 1000)
            audit = AuditEntry(
                audit_id=audit_id,
                source_name="ai_judge",
                query=company_name,
                status="hit",
                latency_ms=latency,
                notes=f"task={task} model={self.model} cost=${cost:.5f}",
            )
            log.debug("ai_judge_call", task=task, model=self.model, cost=cost, latency_ms=latency)
            return text, audit, cost

        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            log.warning("ai_judge_error", task=task, error=str(exc))
            audit = AuditEntry(
                audit_id=audit_id,
                source_name="ai_judge",
                query=company_name,
                status="error",
                latency_ms=latency,
                notes=str(exc),
            )
            return "", audit, 0.0

    async def disambiguate(
        self,
        queried_name: str,
        country: str,
        candidate_a: dict[str, str],
        candidate_b: dict[str, str],
        row_cost_so_far: float = 0.0,
    ) -> tuple[DisambiguateResult | None, AuditEntry, float]:
        prompt = build_disambiguate_prompt(queried_name, country, candidate_a, candidate_b)
        text, audit, cost = await self._call(
            prompt, row_cost_so_far, queried_name, "disambiguate"
        )
        result = _parse_json(text, DisambiguateResult)
        return result, audit, cost

    async def extract_contact_from_prose(
        self,
        company_name: str,
        country: str,
        text: str,
        row_cost_so_far: float = 0.0,
    ) -> tuple[ExtractContactResult | None, AuditEntry, float]:
        prompt = build_extract_contact_prompt(company_name, country, text)
        raw, audit, cost = await self._call(
            prompt, row_cost_so_far, company_name, "extract_contact"
        )
        result = _parse_json(raw, ExtractContactResult)
        return result, audit, cost

    async def translate_name(
        self,
        company_name: str,
        country: str,
        language: str,
        row_cost_so_far: float = 0.0,
    ) -> tuple[TranslateNameResult | None, AuditEntry, float]:
        prompt = build_translate_name_prompt(company_name, country, language)
        raw, audit, cost = await self._call(
            prompt, row_cost_so_far, company_name, "translate_name"
        )
        result = _parse_json(raw, TranslateNameResult)
        return result, audit, cost

    @property
    def run_cost(self) -> float:
        return self._run_cost


_T = TypeVar("_T", bound=BaseModel)


def _parse_json(text: str, model_cls: type[_T]) -> _T | None:
    # Strip markdown code fences if model wraps JSON in ```json ... ```
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1] if cleaned.count("```") >= 2 else cleaned
        cleaned = cleaned.lstrip("json").strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
        return model_cls(**data)
    except Exception as exc:
        log.warning("ai_judge_parse_error", error=str(exc), raw=text[:200])
        return None


def _skip_audit(audit_id: str, company: str, task: str, reason: str) -> AuditEntry:
    return AuditEntry(
        audit_id=audit_id,
        source_name="ai_judge",
        query=company,
        status="skip",
        latency_ms=0,
        notes=f"task={task} reason={reason}",
    )
