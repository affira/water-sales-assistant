# Production image for the Norvale sales assistant API (synthetic data only).
# Build:  docker build -t water-sales-assistant .
# Run:    docker run --rm -p 8000:8000 --env-file .env water-sales-assistant
# Contains no secrets: the API key and all settings come from environment variables at run time.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Unprivileged user; the app only reads its files, so they stay owned by root.
RUN groupadd --system app && useradd --system --gid app --no-create-home --no-log-init app

# Dependencies first: this layer is reused until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code and the synthetic data the tools read at run time.
COPY aquaassist/ ./aquaassist/
COPY data/ ./data/
# Extracted service records (built by `python -m aquaassist.ingest`). The [x] wildcard makes
# this copy optional: without it the image still builds and /health reports the index missing.
COPY inde[x]/ ./index/

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "aquaassist.api:app", "--host", "0.0.0.0", "--port", "8000"]
