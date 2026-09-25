# Scrobbler API: gunicorn serving the Flask app, metrics on :9100.
FROM ghcr.io/astral-sh/uv:0.7.21 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home scrobbler
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY gunicorn.conf.py docker/api-entrypoint.sh ./
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/scrobbler-metrics \
    BIND=0.0.0.0:8000 \
    METRICS_PORT=9100 \
    OPENAPI_DOCS_ENABLED=1
USER scrobbler
EXPOSE 8000 9100
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
ENTRYPOINT ["./api-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py"]
