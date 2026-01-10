ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-wheel

# Set working directory
WORKDIR /app

# Install uv
RUN pip3 install --no-cache-dir --break-system-packages uv

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* README.md ./

# Copy application source
COPY src/ ./src/

# Install dependencies (no dev deps, frozen lockfile)
RUN uv sync --no-dev --frozen

# Copy example template
COPY templates/_example.yaml ./templates/_example.yaml

# Copy run script for s6-overlay
COPY labelable/run.sh /etc/services.d/labelable/run
RUN chmod a+x /etc/services.d/labelable/run

# Expose port (for direct access if enabled)
EXPOSE 7979
