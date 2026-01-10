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
```

## Architecture

```
src/labelable/
├── app.py              # FastAPI application factory
├── config.py           # Pydantic settings, loads config.yaml
├── queue.py            # In-memory print queue with expiry
├── __main__.py         # Entry point (uv run labelable)
├── models/             # Pydantic data models
│   ├── printer.py      # Printer configuration
│   ├── template.py     # Label template definitions
│   └── job.py          # Print job representation
├── printers/           # Printer implementations
│   ├── base.py         # Abstract Printer interface with cached status
│   ├── zpl.py          # Zebra ZPL (TCP/serial)
│   ├── epl2.py         # Zebra EPL2 (TCP/serial)
│   └── ptouch.py       # Brother P-Touch (stubbed)
├── templates/          # Template engine
│   ├── engine.py       # Abstract TemplateEngine
│   ├── jinja_engine.py # Jinja2 implementation
│   └── bitmap_engine.py# PIL-based (stubbed)
└── api/
    ├── routes.py       # REST API endpoints
    └── ui.py           # FastUI web interface
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

templates_dir: ./templates
```

### Template Files (templates/*.yaml)

Templates are YAML files with Jinja2 content for generating printer commands.

**Supported field types:**
- `string` - Text input
- `integer` - Numeric input (integers only)
- `float` - Numeric input (decimals allowed)
- `boolean` - Checkbox (true/false)
- `select` - Radio buttons (predefined options list)
- `datetime` - Auto-populated with current timestamp (uses `format` for strftime)
- `user` - Auto-populated from Home Assistant user (via `X-Hass-User-Id` header)

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
- User ID passed via `X-Hass-User-Id` header when accessed via HA Ingress
- Maps to display names via `user_mapping` in config
- Falls back to `default_user` if no mapping found

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
