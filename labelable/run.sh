#!/usr/bin/env bashio

# Read configuration from Home Assistant add-on options
CONFIG_FILE=$(bashio::config 'config_file')
TEMPLATES_DIR=$(bashio::config 'templates_dir')

# Create config directory if needed
mkdir -p "$(dirname "$CONFIG_FILE")"
mkdir -p "$TEMPLATES_DIR"

# Set environment variables for the application
export LABELABLE_CONFIG_FILE="$CONFIG_FILE"
export LABELABLE_HOST="0.0.0.0"
export LABELABLE_PORT="7979"

# Create default config if it doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'EOF'
# Labelable Configuration
# See https://github.com/ozonejunkieau/labelable for documentation

queue_timeout_seconds: 300
templates_dir: /config/labelable/templates

# API key for external access (optional)
# If set, API requests must include this key via:
#   - X-API-Key header
#   - Authorization: Bearer <key> header
#   - api_key query parameter
# Requests via Home Assistant Ingress (sidebar) don't need the key.
# api_key: your-secret-key-here

# User mapping: Home Assistant user ID -> display name
# Get user IDs from: Settings → People → click user → see URL
user_mapping: {}
  # "abc123-def456-user-uuid": "Your Name"

default_user: ""

# Printer definitions
printers: []
  # - name: my-printer
  #   type: zpl  # or epl2
  #   connection:
  #     type: tcp
  #     host: 192.168.1.100
  #     port: 9100
  #   healthcheck:
  #     interval: 60
  #     command: "~HS"  # ZPL: ~HS, EPL2: UQ
EOF
    bashio::log.info "Created default configuration at $CONFIG_FILE"
    bashio::log.info "Please edit this file to add your printers."
fi

# Copy example template if templates directory is empty
if [ -z "$(ls -A "$TEMPLATES_DIR" 2>/dev/null)" ]; then
    if [ -f "/app/templates/_example.yaml" ]; then
        cp /app/templates/_example.yaml "$TEMPLATES_DIR/_example.yaml"
        bashio::log.info "Copied example template to $TEMPLATES_DIR"
    fi
fi

bashio::log.info "Starting Labelable..."
bashio::log.info "Config file: $CONFIG_FILE"
bashio::log.info "Templates directory: $TEMPLATES_DIR"
bashio::log.info "Web UI available via Home Assistant sidebar"

# Start the application
cd /app
exec uv run python -m labelable
