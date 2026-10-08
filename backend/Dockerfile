FROM python:3.11.11-slim AS base

WORKDIR /app

# PyPI 源。**默认值是阿里云，不是清华**——清华镜像在部分网络环境（走 TUN/代理的
# fake-IP 网段）会直接返回 403，表现为
#   ERROR: Could not find a version that satisfies the requirement pytest<9.0.0,>=8.2.0
#   (from versions: none)
# 注意 "from versions: none" 有欺骗性：TCP 与 TLS 都通、DNS 也解析得出地址，
# 只有取 index 页面时被拒，所以看起来像"包不存在"，其实是源不可用。
# 需要换源时不必改文件：
#   docker compose build --build-arg PIP_INDEX=https://pypi.org/simple backend
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple

RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 appuser

# Install runtime deps only (test deps live in requirements-dev.txt and are
# installed by CI or the `dev` target below). requirements.lock pins exact
# versions verified against a running container.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock -i ${PIP_INDEX}

# Copy source code
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini .

# 验收脚本（scripts/verify_story_model）随镜像一起带上，这样对着**跑着的容器**
# 就能随时验一遍规格第 7 节那八条问题，不必再挂载源码：
#   docker compose exec -T backend python /app/scripts/verify_story_model/check_spec_questions.py
COPY ./scripts ./scripts

EXPOSE 8000

USER appuser

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 ${DEV_MODE:+--reload}"]

# ── Dev stage: base + test dependencies (local development / tests) ───────
FROM base AS dev

USER root

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt -i ${PIP_INDEX}

USER appuser