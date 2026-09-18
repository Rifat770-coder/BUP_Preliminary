"""High-level orchestrator that turns operator notes into validated directives.

This module wraps the raw LLM client with deterministic guardrails and a
bounded retry-with-repair loop. It returns the final, fully validated list of
interpretations ready for the constraint compiler.
"""
from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.guardrails.validator import GuardrailError, validate_interpretations
from app.llm.client import LLMError, generate_structured
from app.llm.prompts import SYSTEM_PROMPT, render_user_prompt


async def interpret_notes(
    settings: Settings,
    notes: list[str],
    capacity_kwh: float,
) -> list[dict[str, Any]]:
    """Return a list of validated directive interpretations (one per note).

    The pipeline:
      1. Build prompts.
      2. Ask the LLM (or mock) for a JSON object with `interpretations`.
      3. Validate against guardrails. On failure, retry once with a repair
         prompt that shows previous errors.
      4. Return the validated list. If both attempts fail, raise LLMError.
    """
    if not notes:
        return []

    user_prompt = render_user_prompt(scenario_id="", notes=notes, capacity_kwh=capacity_kwh)
    sys_prompt = SYSTEM_PROMPT

    last_error: str | None = None
    last_payload: dict[str, Any] | None = None
    for attempt in range(2):
        prompt = user_prompt
        if last_error is not None:
            prompt = (
                user_prompt
                + "\n\n--- REPAIR HINT ---\nYour previous response failed validation with:\n"
                + last_error
                + "\nYou MUST return a JSON object with key `interpretations`. "
                + "Fix ONLY the offending fields and keep every other interpretation unchanged. "
                + "Use ONLY these directive_type values: solar_reduction, minimum_battery_reserve, "
                + "no_charge_window, no_discharge_window, max_grid_window, no_op."
            )

        try:
            payload = await generate_structured(
                settings=settings,
                system_prompt=sys_prompt,
                user_prompt=prompt,
                capacity_kwh=capacity_kwh,
            )
        except LLMError as exc:  # pragma: no cover - depends on provider
            raise

        last_payload = payload
        try:
            interpretations = _extract_interpretations(payload, notes)
            validated, errors = validate_interpretations(
                interpretations, notes_count=len(notes), battery_capacity=capacity_kwh
            )
            if not errors:
                return validated
            last_error = "; ".join(errors)
        except GuardrailError as exc:
            last_error = str(exc)
        except (KeyError, ValueError, TypeError) as exc:
            last_error = f"could not parse LLM payload: {exc}"

    raise LLMError(
        "LLM interpretation failed guardrails after retry: " + (last_error or "unknown error")
    )


def _extract_interpretations(payload: Any, notes: list[str]) -> list[dict[str, Any]]:
    """Pull `interpretations` list out of any payload shape we accept."""
    if isinstance(payload, dict):
        if "interpretations" in payload and isinstance(payload["interpretations"], list):
            return payload["interpretations"]
        # Sometimes models echo the structure as the top level array (rare).
        for key, value in payload.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    if isinstance(payload, list):
        return payload
    raise GuardrailError("LLM response did not contain an `interpretations` array")
