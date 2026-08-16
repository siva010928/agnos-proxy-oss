# Agnos Proxy - Production Image
# Multi-stage: build frontend + install Python deps, then slim runtime
FROM node:20-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app

# System deps for asyncpg, cryptography, tiktoken
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Install Poetry. Must be 2.x: the project uses PEP 621 [project] metadata with
# `dynamic = ["dependencies"]` (deps still declared in [tool.poetry.dependencies]),
# and poetry.lock is lock-version 2.1 - both unreadable by Poetry 1.x.
RUN pip install --no-cache-dir poetry==2.4.1
COPY pyproject.toml poetry.lock ./
# --no-root: install ONLY the locked dependencies here (the gateway/ source is copied
# in the next layer and run from WORKDIR via `python gateway_server.py`, so the root
# package is never pip-installed - this also keeps the deps layer cache-stable and
# avoids needing README.md / the package tree at this stage).
RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-root --no-interaction --no-ansi

# Copy application code
COPY gateway/ ./gateway/
COPY gateway_server.py ./
COPY scripts/ ./scripts/
COPY demo/ ./demo/
COPY data/ ./data/

# Copy built frontend
COPY --from=frontend-build /build/dist ./frontend/dist

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

EXPOSE 8090
CMD ["python", "gateway_server.py"]
