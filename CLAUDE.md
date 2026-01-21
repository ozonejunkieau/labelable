# Labelable - Developer Guide

## Project Overview

Labelable is a label printing API and web UI designed for home use, primarily as a Home Assistant add-on. It supports Zebra ZPL and EPL2 thermal printers via TCP or serial connections.

## Development Setup

This project uses [UV](https://github.com/astral-sh/uv) for dependency management and [just](https://github.com/casey/just) as a command runner.

```bash
# Install dependencies
just install     # or: uv sync --all-extras

# Run development server
just run         # starts on http://localhost:7979

# Run tests
just test        # or: uv run pytest

# Format and lint
just fmt
just lint

# Install pre-commit hook (runs lint + tests before each commit)
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
```

## Architecture

```
src/labelable/
├── app.py              # FastAPI application factory
├── config.py           # Pydantic settings, loads config.yaml, HA auto-discovery
├── queue.py            # In-memory print queue with expiry
├── __main__.py         # Entry point (uv run labelable)
├── models/             # Pydantic data models
│   ├── printer.py      # Printer configuration (TCP, serial, HA connections)
│   ├── template.py     # Label template definitions
│   └── job.py          # Print job representation
├── printers/           # Printer implementations
│   ├── base.py         # Abstract Printer interface with cached status
│   ├── zpl.py          # Zebra ZPL (TCP/serial/HA)
│   ├── epl2.py         # Zebra EPL2 (TCP/serial/HA)
│   └── ptouch.py       # Brother P-Touch (stubbed)
├── templates/          # Template engine
│   ├── engine.py       # Abstract TemplateEngine
│   ├── jinja_engine.py # Jinja2 implementation
│   └── bitmap_engine.py# PIL-based (stubbed)
└── api/
    ├── routes.py       # REST API endpoints
    └── ui.py           # FastUI web interface

custom_components/zebra_printer/   # HA Custom Integration
├── __init__.py         # Integration setup, platform loading
├── manifest.json       # HACS metadata, DHCP discovery config
├── config_flow.py      # Manual IP + DHCP discovery flows
├── const.py            # Constants (OUI prefixes, commands)
├── coordinator.py      # DataUpdateCoordinator for polling
├── entity.py           # Base entity with device info
├── binary_sensor.py    # Online, head_open, paper_out, etc.
├── sensor.py           # Model, firmware, labels_printed, etc.
├── services.py         # print_raw, calibrate, feed handlers
├── services.yaml       # Service definitions for UI
├── strings.json        # Config flow UI strings
├── translations/       # Localization
└── protocol/           # Printer protocol implementations
    ├── base.py         # Abstract protocol + PrinterStatus dataclass
    ├── zpl.py          # ZPL parsing (~HS, ~HI, ~HQOD)
    └── epl2.py         # EPL2 parsing (UQ)
```

## Configuration

### Main config (config.yaml)

```yaml
queue_timeout_seconds: 300
default_user: ""
user_mapping:
  "ha-user-uuid-here": "Display Name"

printers:
  - name: zpl-printer
    type: zpl
    connection:
      type: tcp
      host: 192.168.1.100
      port: 9100
    healthcheck:
      interval: 60
      command: "~HS"

  - name: epl2-printer
    type: epl2
    connection:
      type: tcp
      host: 192.168.1.101
      port: 1883
    healthcheck:
      interval: 60
      command: "UQ"

  # HA Integration connection (uses zebra_printer integration as transport)
  - name: ha-printer
    type: zpl
    connection:
      type: ha
      device_id: warehouse_zebra  # Device ID from HA integration
      # ha_url: http://supervisor/core  # Default, override if needed
      # ha_token: null  # Uses SUPERVISOR_TOKEN when running as add-on

templates_dir: ./templates
```

### HA Connection Auto-Discovery

When running as a Home Assistant add-on with no printers configured, Labelable will
automatically discover printers from the `zebra_printer` HA integration:

```yaml
# Empty printers list triggers auto-discovery
printers: []
```

Auto-discovery queries the HA API for `binary_sensor.*_online` entities from the
`zebra_printer` integration and creates `HAConnection` configurations automatically.

### Template Files (templates/*.yaml)

Templates are YAML files with Jinja2 content for generating printer commands.

**Supported field types:**
- `string` - Text input
- `integer` - Numeric input (integers only)
- `float` - Numeric input (decimals allowed)
- `boolean` - Checkbox (true/false)
- `select` - Radio buttons (predefined options list)
- `datetime` - Auto-populated with current timestamp (uses `format` for strftime)
- `user` - Auto-populated from Home Assistant user (via `X-Remote-User-Display-Name` header)

Example template:
```yaml
name: leftovers
description: Food container label
dimensions:
  width_mm: 40
  height_mm: 28
supported_printers:
  - zpl
fields:
  - name: name
    type: string
    required: true
  - name: gluten_free
    type: boolean
    default: false
  - name: caution
    type: select
    options: ["", "DOG FOOD", "Spicy"]
  - name: created_at
    type: datetime
    format: "%Y-%m-%d %H:%M"
  - name: created_by
    type: user
template: |
  ^XA
  ^FO50,50^FD{{ name }}^FS
  {% if gluten_free %}^FD!!! GF !!!^FS{% endif %}
  ^XZ
```

## Key Design Patterns

### Async Architecture
- All printer operations are async using `asyncio`
- Each printer runs its own background task for queue processing
- TCP connections use `asyncio.open_connection`
- Serial connections use `pyserial` with executor

### Cached Printer Status
- Printers cache their online status (30s TTL)
- Background worker refreshes status every `healthcheck.interval` seconds
- Model info is parsed from healthcheck responses (~HI for ZPL, UQ for EPL2)
- Avoids blocking page renders while checking printer status

### Print Queue
- In-memory `asyncio.Queue` per printer
- Jobs expire after configurable timeout (default 5 min)
- Queue is lost on restart (intentional - no persistence)

### Home Assistant Integration
- HA Ingress provides user headers:
  - `X-Remote-User-Id` - User's UUID
  - `X-Remote-User-Name` - Username
  - `X-Remote-User-Display-Name` - Display name
- User ID can be mapped via `user_mapping` in config
- Falls back to display name, then username, then `default_user`

### HA Printer Status Sensors
To monitor printer status in Home Assistant dashboards or automations, use REST sensors.
Add to your HA `configuration.yaml`:

```yaml
rest:
  - resource: http://localhost:7979/api/v1/printers
    scan_interval: 60
    sensor:
      - name: "Label Printer Status"
        value_template: "{{ value_json[0].online }}"
        json_attributes_path: "$[0]"
        json_attributes:
          - name
          - type
          - queue_size
          - last_checked

# Or for a specific printer:
sensor:
  - platform: rest
    name: "Warehouse Printer"
    resource: http://localhost:7979/api/v1/printers/warehouse-zpl
    value_template: "{{ 'Online' if value_json.online else 'Offline' }}"
    json_attributes:
      - queue_size
      - last_checked
    scan_interval: 60
```

Note: Replace `localhost:7979` with the add-on's internal hostname if accessing from HA Core.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/printers` | List printers with status |
| GET | `/api/v1/templates` | List available templates |
| GET | `/api/v1/templates/{name}` | Get template details |
| POST | `/api/v1/print/{template}` | Submit print job |

## Printer Commands Reference

### ZPL (Zebra Programming Language)
- `~HS` - Host status query (healthcheck)
- `~HI` - Host identification (model info)
- `^XA` / `^XZ` - Label start/end
- `^FO` - Field origin (position)
- `^FD` / `^FS` - Field data start/end
- `^CF` - Change font
- `^A0` - Scalable font

### EPL2 (Eltron Programming Language)
- `UQ` - Status query (healthcheck, returns model info)
- `N` - Clear image buffer
- `A` - ASCII text
- `B` - Barcode
- `P` - Print label

## Adding a New Printer Type

1. Create `src/labelable/printers/newtype.py`
2. Inherit from `BasePrinter`
3. Implement all abstract methods (connect, disconnect, is_online, print_raw)
4. Add to `PrinterType` enum in `models/printer.py`
5. Register in printer factory in `printers/__init__.py`

## Adding a New Template Engine

1. Create `src/labelable/templates/newengine.py`
2. Inherit from `BaseTemplateEngine`
3. Implement `render(template, context) -> bytes`
4. Register in engine factory

## Release Process

Before creating a tagged release, complete these steps:

### 0. Verify Lint and Tests Pass
**CRITICAL**: Always run lint and tests before committing:
```bash
just lint        # or: uv run ruff check src tests
just test        # or: uv run pytest
```
Do NOT commit if either fails. Fix issues first.

### 1. Update Version Numbers
Update version in both files (they must match):
- `pyproject.toml` - `version = "X.Y.Z"`
- `labelable/config.yaml` - `version: "X.Y.Z"`

### 2. Update Changelog
Edit `labelable/CHANGELOG.md` (Home Assistant add-on changelog):
- Add new version heading: `## X.Y.Z`
- List changes as bullet points with `-` prefix
- Place new version at the top (reverse chronological order)
- Keep entries concise and user-focused

Example:
```markdown
## 0.2.0

- Add support for Brother P-Touch printers
- Fix template validation error messages
- Improve printer status caching

## 0.1.0

- Initial release
...
```

### 3. Commit and Tag
```bash
git add -A
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
git push && git push --tags
```

### 4. Verify Build
GitHub Actions will automatically build container images. Verify the build succeeds:
```bash
gh run list --limit 1
```

## Home Assistant Ingress

The UI must work both directly and via HA Ingress proxy. Key considerations:

### URL Handling
- HA Ingress sets `X-Ingress-Path` header with the proxy prefix
- `IngressPathMiddleware` in `app.py` sets ASGI `root_path` from this header
- FastUI needs two meta tags for correct URL handling:
  - `fastui:APIRootUrl` - Base URL for API calls (e.g., `/api/hassio_ingress/<token>/api`)
  - `fastui:APIPathStrip` - Prefix to strip from browser path before appending to APIRootUrl
- Form `submit_url` must include full path since FastUI's `useRequest()` doesn't transform URLs

### Testing Ingress Changes
1. Update version, commit, tag, and push
2. Wait for GitHub Actions build to complete
3. Update add-on in Home Assistant
4. Test both navigation and form submission via HA sidebar
