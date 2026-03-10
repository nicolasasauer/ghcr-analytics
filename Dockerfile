# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Only copy the dependency manifest first to leverage layer caching
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="GHCR-Pulse" \
      org.opencontainers.image.description="Self-hosted GHCR container analytics dashboard" \
      org.opencontainers.image.source="https://github.com/nicolasasauer/ghcr-analytics"

# Security: run as non-root
RUN addgroup --system ghcr && adduser --system --ingroup ghcr ghcr

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/

# Ensure the data directory is writable by the app user
RUN mkdir -p /data && chown ghcr:ghcr /data

USER ghcr

# Environment defaults (all can be overridden at runtime)
ENV DB_PATH=/data/stats.db \
    UPDATE_INTERVAL_HOURS=6 \
    GITHUB_TOKEN="" \
    AUTH_USER="" \
    AUTH_PASSWORD=""

EXPOSE 8000

# Use exec form so signals are forwarded correctly
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
