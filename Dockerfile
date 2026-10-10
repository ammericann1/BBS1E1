# ─────────────────────────────────────────────────────────
# Instagram Reel Automation — Production Image
# Railway deployment with FFmpeg and persistent volume
# ─────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# System dependencies: FFmpeg, curl (healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Verify FFmpeg
RUN ffmpeg -version && ffprobe -version

# App directory
WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Create non-root user
RUN useradd --create-home appuser && \
    mkdir -p /data/sources /data/clips /data/state && \
    chown -R appuser:appuser /data /app

USER appuser

# Environment defaults (overridden by Railway env vars)
ENV DATA_DIR=/data
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# The persistent volume is mounted at /data by Railway
VOLUME ["/data"]

# Health check (process is alive)
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD pgrep -f "python" || exit 1

# Default: run the scheduled worker
# Override with Railway start command if needed
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["run-worker"]
