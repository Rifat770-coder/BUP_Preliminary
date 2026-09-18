FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app
COPY scripts /app/scripts
COPY README.md /app/README.md

EXPOSE 8000

# No default LLM_PROVIDER — operators MUST set LLM_PROVIDER, LLM_API_KEY,
# LLM_MODEL, and (for openai_compatible) LLM_BASE_URL explicitly.
# When APP_ENV=production, the app refuses to boot if LLM_PROVIDER=mock or
# the API key is missing (see app/config.py:ProductionGuardError).
ENV HOST=0.0.0.0 \
    PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, json; \
r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); \
d = json.loads(r.read()); \
import sys; sys.exit(0 if d.get('status') == 'ok' else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
