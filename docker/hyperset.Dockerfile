# Build the browser bundle in its own stage so the runtime image stays Python-only.
FROM node:22-alpine AS playground-ui

WORKDIR /playground-ui
COPY playground/ui/package.json ./package.json
COPY playground/ui/package-lock.json ./package-lock.json
COPY packages/chat-ui /packages/chat-ui
# The chat UI is a local file dependency. Install it from the copied package
# source; npm ci cannot consume the platform-specific file link in this image.
RUN npm install --no-audit --no-fund --package-lock=false
COPY playground/ui ./
RUN npm run build

# Builds the Hyperset Python package image. One image, multiple services
# (migrate/api/worker/etc.) select behavior via the compose `command:`,
# matching how the official Superset image works (one image, entrypoint
# chosen per service) rather than a separate image per role.
FROM python:3.12-slim AS base

# `git` is a runtime dependency, not a build tool: `hyperset context sync`
# reads the customer's configured context repository through the git CLI
# (hy-gh-43).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependency layer cached separately from source so a source-only change
# doesn't reinstall the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra planner --extra mcp --no-install-project

COPY hyperset ./hyperset
# The checked-in base configuration (config/base.yaml) is loaded at serve startup
# (hy-ax9w, ADR-0035): without it the layered-config load fails closed and the server
# cannot boot in the container. `config.BASE_CONFIG` resolves to <app>/config/base.yaml.
COPY config ./config
COPY playground ./playground
COPY docs ./docs
COPY --from=playground-ui /playground-ui/dist ./playground/ui/dist
COPY alembic.ini ./alembic.ini
COPY README.md ./README.md
RUN uv sync --frozen --extra planner --extra mcp

RUN useradd --create-home --uid 1000 hyperset
# Created (and owned) before the volume is mounted: Docker seeds a named
# volume's ownership from the image path, so without this the Git context
# cache mount would be root-owned and unwritable by the runtime user.
RUN install -d -o hyperset -g hyperset /var/lib/hyperset/git-context
USER hyperset

# The virtualenv is baked at build time (the two `uv sync --frozen` above) and is
# owned by root, while the container runs as the unprivileged `hyperset` user.
# Without this, `uv run` re-resolves the project into `.venv` at every container
# start, and that write fails against the root-owned venv on a clean checkout's
# first `make up` (GitHub #320). `UV_NO_SYNC=1` makes `uv run` use the baked
# environment as-is -- the project's `hyperset` console script is already installed
# in it by the `uv sync --frozen` at line 41 -- and skip the start-time sync.
ENV UV_NO_SYNC=1
ENTRYPOINT ["uv", "run", "hyperset"]
CMD ["db", "upgrade"]
