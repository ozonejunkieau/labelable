# Labelable

A general purpose label printing API and web UI for home use.

## Features

- **Multiple Printer Support**: Zebra ZPL, Zebra EPL2, and Brother P-Touch (future)
- **Template System**: Define label templates in YAML with Jinja2 templating
- **REST API**: Print labels programmatically from Home Assistant or other systems
- **Web UI**: Simple browser-based interface for manual label printing
- **Print Queue**: Automatic queuing when printers are offline
- **Home Assistant Integration**: Ingress support, user mapping, API authentication

## Installation

### Home Assistant Add-on (Recommended)

1. Add this repository to Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store**
   - Click **⋮** (menu) → **Repositories**
   - Add: `https://github.com/ozonejunkieau/labelable`

2. Install the **Labelable** add-on

3. Configure your printers in `/config/labelable/config.yaml`

4. Access via the Home Assistant sidebar

### Docker

```bash
docker build -t labelable .
docker run -p 7979:7979 \
  -v ./config.yaml:/app/config.yaml \
  -v ./templates:/app/templates \
  labelable
```

### Local Development

```bash
uv sync
just run
```

## Configuration

Copy `config.example.yaml` to `config.yaml`:

```yaml
queue_timeout_seconds: 300
templates_dir: ./templates

# API key for external access (optional)
# If set, requests must authenticate (see API Authentication below)
api_key: your-secret-key-here

# Home Assistant user mapping
user_mapping:
  "abc123-user-uuid": "Test User"
default_user: ""

printers:
  - name: warehouse-zpl
    type: zpl
    connection:
      type: tcp
      host: 192.168.1.100
      port: 9100
    healthcheck:
      interval: 60
      command: "~HS"
```

## Templates

Create label templates in `templates/`. Files starting with `_` are ignored (use for examples).

See `templates/_example.yaml` for a complete example with all field types.

```yaml
name: shipping-label
description: Basic shipping address label
dimensions:
  width_mm: 100
  height_mm: 50
supported_printers:  # Printer names from your config.yaml
  - warehouse-zpl
fields:
  - name: name
    type: string
    required: true
  - name: printed_at
    type: datetime
    format: "%Y-%m-%d %H:%M"
template: |
  ^XA
  ^FO50,50^A0N,30,30^FD{{ name }}^FS
  ^FO50,100^A0N,20,20^FD{{ printed_at }}^FS
  ^FO50,130^SN1,1,N^FD of {{ quantity }}^FS
  ^PQ{{ quantity }}
  ^XZ
```

### Field Types

| Type | Description |
|------|-------------|
| `string` | Text input |
| `integer` | Whole number |
| `float` | Decimal number |
| `boolean` | True/false checkbox |
| `select` | Radio buttons from predefined `options` list |
| `datetime` | Auto-populated with current time (uses `format` for strftime) |
| `user` | Auto-populated from Home Assistant user or `default_user` |

### Built-in Template Variables

| Variable | Description |
|----------|-------------|
| `quantity` | Number of labels being printed (for `^PQ` and `^SN` commands) |

## API Usage

See [docs/PYTHON_CLIENT.md](docs/PYTHON_CLIENT.md) for complete API documentation with examples.

### Quick Example

```python
import requests

response = requests.post(
    "http://localhost:7979/api/v1/print/shipping-label",
    headers={"X-API-Key": "your-secret-key"},  # omit if no api_key configured
    json={"data": {"name": "John Doe"}, "printer": "warehouse-zpl", "quantity": 1}
)
```

### From Home Assistant Automations

When running as an add-on, use the internal Docker hostname:

```yaml
# configuration.yaml
rest_command:
  print_label:
    url: "http://local-labelable:7979/api/v1/print/{{ template }}"
    method: POST
    content_type: "application/json"
    payload: '{"data": {{ data | tojson }}, "printer": "{{ printer }}"}'
```

```yaml
# automation
action:
  - service: rest_command.print_label
    data:
      template: shipping-label
      printer: warehouse-zpl
      data:
        name: "Package for {{ trigger.event.data.recipient }}"
```

### API Authentication

If `api_key` is set in config, requests must authenticate via one of:

| Method | Example |
|--------|---------|
| `X-API-Key` header | `X-API-Key: your-secret-key` |
| `Authorization` header | `Authorization: Bearer your-secret-key` |
| Query parameter | `?api_key=your-secret-key` |

**Note:** Requests via Home Assistant Ingress (sidebar UI) are automatically authenticated and don't need the API key.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/printers` | List printers with status |
| GET | `/api/v1/templates` | List available templates |
| GET | `/api/v1/templates/{name}` | Get template details |
| POST | `/api/v1/print/{template}` | Submit print job |

**Response Codes:**
- `200` - Label printed successfully
- `202` - Label queued (printer offline)
- `400` - Invalid request
- `401` - Invalid or missing API key
- `404` - Template or printer not found

## Development

```bash
just install    # Install dependencies
just run        # Run dev server (auto-reload)
just test       # Run tests
just fmt        # Format code
just lint       # Lint code
```

### Creating a Release

1. Go to **Actions** → **Create Release**
2. Click **Run workflow**
3. Enter the version (e.g., `0.2.0`)
4. Click **Run workflow**

This automatically:
- Updates version in `pyproject.toml` and `ha-addon/config.yaml`
- Creates a git tag
- Creates a GitHub release
- Triggers the build workflow to push Docker images to GHCR

## Tested Hardware

| Printer | Connection | Protocol |
|---------|------------|----------|
| Zebra GK420d | Ethernet (TCP 9100) | ZPL |
| Zebra LP2844 | RS232-WiFi adapter | EPL2 |

## Development

This project was developed primarily using [Claude Code](https://claude.ai/code), Anthropic's AI coding assistant. Human oversight and direction provided by [@ozonejunkieau](https://github.com/ozonejunkieau).

## License

MIT
