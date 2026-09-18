"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    return "" if val is None else val


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    llm_timeout_seconds: float
    host: str
    port: int
    production_mode: bool


class ProductionGuardError(RuntimeError):
    """Raised at startup when production-mode invariants are violated."""


def load_settings() -> Settings:
    provider = _env("LLM_PROVIDER", "mock").strip().lower() or "mock"
    api_key = _env("LLM_API_KEY", "")
    model = _env("LLM_MODEL", "")
    base_url = _env("LLM_BASE_URL", "")
    timeout_raw = _env("LLM_TIMEOUT_SECONDS", "12")
    try:
        timeout = float(timeout_raw) if timeout_raw else 12.0
    except ValueError:
        timeout = 12.0
    port_raw = _env("PORT", "8000")
    try:
        port = int(port_raw) if port_raw else 8000
    except ValueError:
        port = 8000
    # APP_ENV values: "production" (or "prod") ⇒ strict; anything else ⇒ permissive.
    env_name = _env("APP_ENV", "").strip().lower()
    production_mode = env_name in {"production", "prod"}

    if production_mode:
        if provider == "mock":
            raise ProductionGuardError(
                "APP_ENV=production but LLM_PROVIDER=mock. "
                "Set LLM_PROVIDER to a real provider (openai_compatible, openai, "
                "groq, gemini) and configure LLM_API_KEY before serving traffic."
            )
        if not api_key:
            raise ProductionGuardError(
                "APP_ENV=production but LLM_API_KEY is empty. "
                "Provide a real API key via environment variable."
            )
        if not model:
            raise ProductionGuardError(
                "APP_ENV=production but LLM_MODEL is empty. "
                "Set LLM_MODEL to the exact provider model identifier."
            )
        if provider in {"openai_compatible", "openai", "groq"} and not base_url and provider != "openai":
            raise ProductionGuardError(
                f"APP_ENV=production but LLM_BASE_URL is empty for provider={provider!r}. "
                "Set LLM_BASE_URL to the provider endpoint."
            )

    return Settings(
        llm_provider=provider,
        llm_api_key=api_key,
        llm_model=model,
        llm_base_url=base_url,
        llm_timeout_seconds=timeout,
        host=_env("HOST", "0.0.0.0"),
        port=port,
        production_mode=production_mode,
    )


# Canonical six directive types, including no_op.
ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

BATTERY_ACTIONS = {"charge", "discharge", "idle"}

EPSILON = 1e-6
TOL_KWH = 0.01
TOL_BDT = 0.01
