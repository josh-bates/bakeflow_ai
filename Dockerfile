# BakeFlow AI application image (BAKE-6; architecture §7.1).
# One image runs the whole backend. BAKE-10 adds a Node build stage whose
# React output is copied to /frontend/dist, which FastAPI then serves.
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

RUN useradd --create-home --uid 10001 bakeflow
USER bakeflow

EXPOSE 8000

# Apply migrations, then serve. Compose starts this only once db is healthy.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
