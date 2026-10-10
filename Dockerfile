# ─────────────────────────────────────────────────────────
# Instagram Reel Automation — Production Image
# Railway deployment with FFmpeg and persistent volume
# ─────────────────────────────────────────────────────────

FROM python:3.12-slim

# System dependencies: FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Verify FFmpeg installation
RUN ffmpeg -version && ffprobe -version

# Set working directory
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Create non-root user and data directories
RUN useradd --create-home appuser && \
    mkdir -p /data/sources /data/clips /data/state && \
    chown -R appuser:appuser /data /app

# Switch to non-root user
USER appuser

# Environment variables
ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The persistent volume will be mounted at /data by Railway
VOLUME ["/data"]

# Default command (can be overridden by Railway)
CMD ["python", "-m", "app.main", "run-worker"]
