FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY README.md ./

EXPOSE 8000

CMD ["sh", "-c", "tortoise -c src.config.MIGRATIONS_CONFIG migrate local && python -m src.seed_modelos && uvicorn src.api:app --host 0.0.0.0 --port 8000"]
