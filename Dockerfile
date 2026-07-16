FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim-bookworm AS base

ENV PATH="/opt/codalith/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/codalith/.venv

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /opt/codalith

COPY pyproject.toml uv.lock README.md ./
COPY external/CodeRAG ./external/CodeRAG
COPY src ./src
COPY configs ./configs
COPY fixtures ./fixtures
COPY benchmarks ./benchmarks

FROM base AS runtime-build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS test-build
COPY tests ./tests
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra dev

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/codalith/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 codalith \
    && mkdir -p /app/data /var/lib/codalith \
    && chown -R codalith:codalith /app /var/lib/codalith
COPY --from=runtime-build --chown=codalith:codalith /opt/codalith /opt/codalith
WORKDIR /app
RUN ln -s /opt/codalith/configs configs \
    && ln -s /opt/codalith/fixtures fixtures \
    && ln -s /opt/codalith/benchmarks benchmarks
USER codalith
ENTRYPOINT ["codalith"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0"]

FROM test-build AS test
WORKDIR /opt/codalith
ENTRYPOINT ["uv", "run", "--frozen", "--no-sync"]
CMD ["pytest", "-q"]
