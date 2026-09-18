"""FastAPI application exposing /health and /optimize-energy."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import __version__
from app.config import load_settings
from app.llm.client import LLMError
from app.schemas import OptimizeRequest, OptimizeResponse
from app.services.energy_service import ServiceError, run_optimization

logger = logging.getLogger("gridwise")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = load_settings()
app = FastAPI(
    title="GridWise LLM Energy Optimizer",
    version=__version__,
    description="Smart campus energy optimization with LLM-assisted operator note interpretation.",
)


@app.on_event("startup")
async def _log_startup_banner() -> None:
    # Never log the API key. Only log non-secret provider / model identifiers.
    import os as _os

    logger.info(
        "startup app_version=%s app_env=%s production_mode=%s llm_provider=%s "
        "llm_model=%s llm_base_url=%s timeout_s=%s host=%s port=%d",
        __version__,
        _os.getenv("APP_ENV", ""),
        settings.production_mode,
        settings.llm_provider,
        settings.llm_model or "(default)",
        settings.llm_base_url or "(default)",
        settings.llm_timeout_seconds,
        settings.host,
        settings.port,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: Request) -> JSONResponse:
    started = time.perf_counter()
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc

    try:
        payload = OptimizeRequest.model_validate(body)
    except (ValidationError, RequestValidationError) as exc:
        raise HTTPException(status_code=400, detail=_format_validation(exc)) from exc

    try:
        response = await run_optimization(settings, payload)
    except ServiceError as exc:
        logger.warning("service error %s: %s", exc.status, exc.detail)
        if exc.status >= 500:
            raise HTTPException(status_code=500, detail=exc.detail) from exc
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except LLMError as exc:
        logger.error("LLM error: %s", exc)
        raise HTTPException(status_code=500, detail=f"LLM error: {exc}") from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "scenario_id=%s notes=%d elapsed_ms=%d total_grid=%.2f total_cost=%.2f",
        payload.scenario_id,
        len(payload.operator_notes),
        elapsed_ms,
        response.total_grid_kwh,
        response.total_cost_bdt,
    )
    return JSONResponse(content=response.model_dump(mode="json"), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# ---------------------------------------------------------------------------
# Minimal static frontend (does not modify /health or /optimize-energy).
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
async def _index() -> FileResponse:
    """Serve the dashboard SPA entry point."""
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


# Serve static assets (CSS, JS). This must NOT shadow existing API routes.
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _format_validation(exc: Any) -> str:
    """Flatten pydantic validation errors into a single safe message."""
    try:
        errs = exc.errors() if hasattr(exc, "errors") else []
    except Exception:  # pragma: no cover - defensive
        errs = []
    if not errs:
        return "invalid request payload"
    parts: list[str] = []
    for err in errs:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "invalid request: " + "; ".join(parts)


if __name__ == "__main__":
    # Honors HOST / PORT environment variables. The Dockerfile currently invokes
    # uvicorn directly; this block lets `python -m app.main` work too.
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )
