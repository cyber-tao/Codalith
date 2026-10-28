FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM oven/bun:1.4.2 AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/bun.lock ./
RUN bun install --frozen-lockfile
COPY dashboard ./
RUN bun run build

FROM python:3.12-slim-bookworm AS git-build
ARG GIT_VERSION=2.55.0
ARG GIT_SHA256=457fdb04dc8728e007d4688695e6912e6f680727920f2a40bf11eacc17505357
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl xz-utils zlib1g-dev
RUN curl --fail --location --silent --show-error "https://www.kernel.org/pub/software/scm/git/git-${GIT_VERSION}.tar.xz" -o /tmp/git.tar.xz \
    && printf '%s  /tmp/git.tar.xz\n' "$GIT_SHA256" | sha256sum --check - \
    && mkdir /tmp/git-src \
    && tar -xJf /tmp/git.tar.xz -C /tmp/git-src --strip-components=1
RUN make -C /tmp/git-src -j2 prefix=/opt/git NO_RUST=YesPlease NO_GETTEXT=YesPlease NO_TCLTK=YesPlease NO_PERL=YesPlease NO_PYTHON=YesPlease NO_CURL=YesPlease NO_EXPAT=YesPlease NO_OPENSSL=YesPlease install \
    && /opt/git/bin/git --no-lazy-fetch --version

FROM python:3.12-slim-bookworm AS base

ENV PATH="/opt/codalith/.venv/bin:/opt/git/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/codalith/.venv

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=git-build /opt/git /opt/git
WORKDIR /opt/codalith

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY skills ./skills
COPY scripts ./scripts
COPY install.sh install.ps1 ./
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

ENV PATH="/opt/codalith/.venv/bin:/opt/git/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODALITH_DASHBOARD_DIR=/opt/codalith/dashboard/dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 codalith \
    && mkdir -p /app/data /var/lib/codalith \
    && chown -R codalith:codalith /app /var/lib/codalith
COPY --from=runtime-build --chown=codalith:codalith /opt/codalith /opt/codalith
COPY --from=git-build /opt/git /opt/git
COPY --from=dashboard --chown=codalith:codalith /dashboard/dist /opt/codalith/dashboard/dist
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
