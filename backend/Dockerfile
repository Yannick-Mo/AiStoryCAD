FROM python:3.11.11-slim AS base

WORKDIR /app

RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 appuser

# Install runtime deps only (test deps live in requirements-dev.txt and are
# installed by CI or the `dev` target below). requirements.lock pins exact
# versions verified against a running container.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock -i https://pypi.tuna.tsinghua.edu.cn/simple

# Copy source code
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini .

EXPOSE 8000

USER appuser

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 ${DEV_MODE:+--reload}"]

# ── Dev stage: base + test dependencies (local development / tests) ───────
FROM base AS dev

USER root

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

USER appuser