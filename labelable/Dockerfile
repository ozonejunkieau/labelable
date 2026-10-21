ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-wheel \
    libdmtx-libs \
    ttf-dejavu

# Set working directory
WORKDIR /app

# Install uv
RUN pip3 install --no-cache-dir --break-system-packages uv

# Copy dependency files
COPY pyproject.toml uv.lock README.md ./

# Copy application source
COPY src/ ./src/

# Install the server extra (no dev deps, frozen lockfile). The mcp group is
# included so mcp_enabled works in the add-on; without it the option only
# logged a warning.
# Use unsafe-best-match to allow fonttools from PyPI when HA wheels are outdated
RUN uv sync --no-dev --extra server --group mcp --frozen --index-strategy unsafe-best-match

# Copy example template
COPY templates/_example.yaml ./templates/_example.yaml

# Copy run script
COPY labelable/run.sh /
RUN chmod a+x /run.sh

# Expose ports (HTTP for ingress, HTTPS for external access)
EXPOSE 7979 7980

ENTRYPOINT [ "/run.sh" ]
