FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy dependency files and README (needed for package metadata)
COPY pyproject.toml uv.lock* README.md ./

# Copy application source (needed before uv sync for local package install)
COPY src/ ./src/

# Install dependencies (without dev dependencies)
RUN uv sync --no-dev --frozen

# Copy example files (user mounts their own config.yaml and templates/)
COPY config.example.yaml ./config.example.yaml
COPY templates/_example.yaml ./templates/_example.yaml

# Create empty directories for user mounts
RUN mkdir -p /app/templates

# Expose port
EXPOSE 7979

# Environment variables (can be overridden)
ENV LABELABLE_HOST=0.0.0.0
ENV LABELABLE_PORT=7979

# Run the application
# Config and templates should be mounted:
#   -v ./config.yaml:/app/config.yaml
#   -v ./templates:/app/templates
CMD ["uv", "run", "python", "-m", "labelable"]
