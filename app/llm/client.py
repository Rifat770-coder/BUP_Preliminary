"""LLM provider abstraction.

A minimal async client that talks to one of the supported providers and
returns a parsed JSON object produced by the model. The client is
intentionally tolerant: if the provider is unavailable, the call returns a
controlled error rather than crashing the whole service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from ..config import Settings

log = logging.getLogger("gridwise.llm")


class LLMError(Exception):
    """Raised when an LLM provider call fails or returns unusable output."""


@dataclass
class _JsonSchema:
    name: str = "directive_interpretation"
    strict: bool = True


JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "interpretations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "note_index": {"type": "integer", "minimum": 0},
                    "applies": {"type": "boolean"},
                    "directive_type": {
                        "type": "string",
                        "enum": [
                            "solar_reduction",
                            "minimum_battery_reserve",
                            "no_charge_window",
                            "no_discharge_window",
                            "max_grid_window",
                            "no_op",
                        ],
                    },
                    "structured_adjustment": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "properties": {
                                    "hours": {
                                        "type": "array",
                                        "items": {"type": "integer", "minimum": 0, "maximum": 23},
                                        "uniqueItems": True,
                                    },
                                    "factor": {"type": "number"},
                                    "minimum_energy_kwh": {"type": "number"},
                                    "max_grid_kwh": {"type": "number"},
                                },
                            },
                        ]
                    },
                    "explanation": {"type": "string", "minLength": 1},
                },
                "required": [
                    "note_index",
                    "applies",
                    "directive_type",
                    "structured_adjustment",
                    "explanation",
                ],
            },
        }
    },
    "required": ["interpretations"],
}


class _MockProvider:
    """A deterministic offline provider used when no API key is configured.

    This keeps the LLM layer present in the pipeline but does not rely on a
    hosted service. It uses a small set of carefully crafted rule-based
    interpreters that already cover the supported paraphrase patterns. The
    organizer-scoring pipeline can replace this with a real provider by
    setting LLM_PROVIDER plus LLM_API_KEY.
    """

    name = "mock"

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout: float,
        capacity_kwh: float,
    ) -> dict[str, Any]:
        from .mock_interpreter import interpret_offline

        notes = _extract_notes(user_prompt)
        if notes is None:
            raise LLMError("mock provider could not parse notes")
        items = interpret_offline(notes, capacity_kwh=capacity_kwh)
        return {"interpretations": items}


class _OpenAICompatibleProvider:
    """Provider for OpenAI, Groq, OpenRouter, Together, etc., that follow the
    /v1/chat/completions contract and accept a json_schema response_format.
    """

    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.llm_base_url.rstrip("/") or "https://api.openai.com/v1"
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model or "gpt-4o-mini"

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout: float,
        capacity_kwh: float,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("LLM_API_KEY is required for openai_compatible provider")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Determinism is enforced by response_format=json_schema (strict=True);
            # we intentionally omit `temperature` because some newer model families
            # (e.g. gpt-5.x) reject `temperature=0` and require their default value.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "directive_interpretation",
                    "schema": JSON_SCHEMA,
                    "strict": True,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except Exception as exc:  # network/timeout errors
            raise LLMError(f"network error: {exc!s}") from exc
        if resp.status_code >= 400:
            # Fall back to json_object response format for providers that
            # don't fully implement json_schema.
            payload["response_format"] = {"type": "json_object"}
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp2 = await client.post(url, headers=headers, json=payload)
            if resp2.status_code >= 400:
                raise LLMError(
                    f"provider returned {resp2.status_code}: {resp2.text[:200]}"
                )
            data = resp2.json()
        else:
            data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"unexpected provider payload: {exc!s}") from exc
        return _safe_parse_json(content)


class _GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model or "gemini-1.5-flash"
        self.base = settings.llm_base_url.rstrip("/") or (
            "https://generativelanguage.googleapis.com/v1beta"
        )

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout: float,
        capacity_kwh: float,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("LLM_API_KEY is required for gemini provider")
        url = (
            f"{self.base}/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                # Omit `temperature` for portability; Gemini uses a default value.
                "responseMimeType": "application/json",
                "responseSchema": JSON_SCHEMA,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
        except Exception as exc:
            raise LLMError(f"network error: {exc!s}") from exc
        if resp.status_code >= 400:
            raise LLMError(
                f"provider returned {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            raise LLMError(f"unexpected provider payload: {exc!s}") from exc
        return _safe_parse_json(text)


_PROVIDER_FACTORIES = {
    "openai_compatible": _OpenAICompatibleProvider,
    "openai": _OpenAICompatibleProvider,
    "groq": _OpenAICompatibleProvider,
    "openrouter": _OpenAICompatibleProvider,
    "together": _OpenAICompatibleProvider,
    "gemini": _GeminiProvider,
    "mock": _MockProvider,
}


def build_provider(settings: Settings):
    """Construct the provider instance for the configured LLM_PROVIDER."""
    name = (settings.llm_provider or "mock").lower()
    factory = _PROVIDER_FACTORIES.get(name, _MockProvider)
    if factory is _OpenAICompatibleProvider:
        return factory(settings)
    if factory is _GeminiProvider:
        return factory(settings)
    return factory()


async def generate_structured(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    capacity_kwh: float,
) -> dict[str, Any]:
    """Invoke the configured LLM and return a parsed JSON dict."""
    provider = build_provider(settings)
    log.info(
        "llm_call provider=%s model=%s", provider.name, settings.llm_model or "(default)"
    )
    return await provider.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout=settings.llm_timeout_seconds,
        capacity_kwh=capacity_kwh,
    )


def _safe_parse_json(content: str) -> dict[str, Any]:
    """Attempt to parse JSON out of a possibly noisy model response."""
    content = content.strip()
    try:
        return json.loads(content)
    except Exception:
        pass
    # Strip code fences if present.
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            pass
    # Try to find first {...} block.
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except Exception:
            pass
    raise LLMError("could not parse LLM JSON output")


_NOTE_BLOCK_RE = re.compile(r"\[note_index=(\d+)\]\s*(.*?)(?=(?:\n\[note_index=)|\Z)", re.S)


def _extract_notes(user_prompt: str) -> Optional[list[str]]:
    """Used by the mock provider to pull notes back out of the prompt."""
    section = user_prompt.split("OPERATOR NOTES (one per item, in order):", 1)
    if len(section) != 2:
        return None
    body = section[1]
    matches = _NOTE_BLOCK_RE.findall(body)
    if not matches:
        return None
    ordered: list[Optional[str]] = [None] * (max(int(idx) for idx, _ in matches) + 1)
    for idx_str, text in matches:
        idx = int(idx_str)
        if idx >= len(ordered):
            ordered.extend([None] * (idx - len(ordered) + 1))
        ordered[idx] = text.strip()
    return [n for n in ordered if n]
