# syntax=docker/dockerfile:1

# ---- frontend build (React SPA, see frontend/) ----
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential/libffi-dev: defensive only, in case a manylinux wheel isn't
# available for `cryptography` on the build platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY validation_core/ ./validation_core/
COPY app/ ./app/
COPY scripts/ ./scripts/
# Built assets only (index.html + hashed JS/CSS under assets/) -- not
# frontend/'s source or node_modules, see app/main.py's FRONTEND_DIST.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN useradd --create-home --uid 1000 lidvalid \
    && mkdir -p /app/data \
    && chown -R lidvalid:lidvalid /app
USER lidvalid

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
