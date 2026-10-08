# Production image. Baseline platform: linux/amd64 (Fiona 1.10.1 publishes no linux/arm64
# wheel, so arm64 builds would need GDAL from source and are not supported here).
#   docker build --platform linux/amd64 -t aereo-geospatial-api .

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# Runtime dependencies only, exactly as locked.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
# Fiona's manylinux wheel bundles GDAL but links the system libexpat.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin aereo \
    && mkdir -p /data \
    && chown aereo:aereo /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AEREO_DATA_DIR=/data
USER aereo
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=4)"]
# One worker: startup recovery and SQLite assume a single process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
