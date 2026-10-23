FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    JARVIS_ENV=production \
    JARVIS_ALLOW_LIVE=false \
    PORT=8000

WORKDIR /app
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -f /run/secrets/build_ca ]; then \
      export PIP_CERT=/run/secrets/build_ca SSL_CERT_FILE=/run/secrets/build_ca; \
    fi; \
    useradd --create-home --uid 10001 --user-group --no-log-init jarvis; \
    pip install --no-cache-dir uv==0.12.23

COPY --chmod=0644 pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -f /run/secrets/build_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/build_ca; \
    fi; \
    uv sync --frozen --no-dev --no-install-project --no-cache
COPY --chown=jarvis:jarvis jarvis_lite ./jarvis_lite

USER jarvis
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4).read()"

CMD ["/bin/sh", "-ec", "exec uvicorn jarvis_lite.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
