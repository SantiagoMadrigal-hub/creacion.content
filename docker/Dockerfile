# syntax=docker/dockerfile:1
# Imagen multi-stage: build con uv, runtime mínimo con ffmpeg y usuario sin privilegios.

FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/
WORKDIR /app

FROM base AS builder
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --python /opt/venv/bin/python .

FROM base AS runtime
RUN useradd --create-home --uid 1000 lavox
COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    LAVOX_LOG_FORMAT=json \
    LAVOX_ENVIRONMENT=production

WORKDIR /home/lavox/workspace
USER lavox

ENTRYPOINT ["lavox-pipeline"]
CMD ["--help"]
