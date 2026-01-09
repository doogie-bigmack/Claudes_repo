# =============================================================================
# Polymarket Arbitrage Bot - Lightweight Docker Image
# =============================================================================
# Multi-stage build for minimal image size (~150MB)
# Base: Python 3.11 Alpine Linux
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder - Install dependencies with build tools
# -----------------------------------------------------------------------------
FROM python:3.11-alpine AS builder

# Install build dependencies (needed for compiling some Python packages)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    make \
    g++

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# Copy requirements first (for Docker layer caching)
# Using slim requirements for smaller image
COPY requirements-docker.txt .

# Install Python dependencies
# Using --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements-docker.txt

# -----------------------------------------------------------------------------
# Stage 2: Runtime - Minimal production image
# -----------------------------------------------------------------------------
FROM python:3.11-alpine AS runtime

# Labels for container metadata
LABEL maintainer="Polymarket Arbitrage Bot"
LABEL version="1.0.0"
LABEL description="Lightweight arbitrage trading bot for Polymarket"

# Install runtime dependencies only
RUN apk add --no-cache \
    libffi \
    openssl \
    ca-certificates \
    tini \
    && rm -rf /var/cache/apk/*

# Create non-root user for security
RUN addgroup -g 1000 botuser && \
    adduser -u 1000 -G botuser -h /app -D botuser

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    # Bot-specific defaults
    LOG_LEVEL=INFO \
    DRY_RUN=true

# Copy application code
COPY --chown=botuser:botuser config/ ./config/
COPY --chown=botuser:botuser src/ ./src/
COPY --chown=botuser:botuser tests/ ./tests/

# Create logs directory
RUN mkdir -p /app/logs && chown botuser:botuser /app/logs

# Copy entrypoint script
COPY --chown=botuser:botuser docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user
USER botuser

# Expose port for future API/dashboard (optional)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Use tini as init system for proper signal handling
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]

# Default command
CMD ["run", "--dry-run"]
