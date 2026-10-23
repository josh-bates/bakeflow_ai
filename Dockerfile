# BakeFlow AI application image (BAKE-6; architecture §7.1).
# One image runs the whole app. A Node stage builds the React UI (BAKE-10);
# only its static output is copied to /frontend/dist, which FastAPI serves.

# --- Stage 1: build the UI. Node and pnpm match the frontend CI. ---
FROM node:22-slim AS frontend

RUN npm install --global pnpm@11.25.0

WORKDIR /frontend

# Lockfile first, so UI code changes reuse the cached install layer.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# --- Stage 2: the backend, which also serves the built UI. ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes reuse the cached layer.
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./

# app/config.py resolves the repository root to "/" here, so the backend
# looks for the build at /frontend/dist.
COPY --from=frontend /frontend/dist /frontend/dist

RUN useradd --create-home --uid 10001 bakeflow
USER bakeflow

EXPOSE 8000

# Apply migrations, then serve. Compose starts this only once db is healthy.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
