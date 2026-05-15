# ADLC Agent Service — multi-stage Docker build
# Uses Python 3.11 per ADLC_Tech_Stack_Config.json#runtime.python_version

# --- Stage 1: Build dependencies ---
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .

# WeasyPrint needs system libs for PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.11-slim

WORKDIR /app

# Runtime system libs for WeasyPrint
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libcairo2 \
    libffi8 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create shared folder mount point
RUN mkdir -p /shared

# Non-root user for security
RUN useradd -m -r appuser && chown -R appuser:appuser /app /shared
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["python", "run.py"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
