# Labelable

A general purpose label printing API and basic UI for home use.

## Overview

Usage will include:
* A direct UI (simple) for entering relevant data for labels.
* An API interface to allow Home Assistant or other Python code to print labels.
* MQTT interface is a future feature (not in initial implementation).

## Configuration

* All configuration in a single YAML file (`config.yaml`).
* No database or persistent state management.
* Printers defined in config with: name, type (ZPL/EPL2/PTouch), connection details.
* Templates defined in separate YAML files with metadata and content.
* Optional API key authentication for external access.
* Google Fonts downloading support for image templates.

## Label Printer Interface

* An agnostic interface to label printers, at minimum checking if the printer is online or offline.
* Known printers that will be used:
  - **Zebra ZPL printers** - Full support (TCP/serial/HA integration)
  - **Zebra EPL2 printers** - Full support (TCP/serial/HA integration)
  - **Brother P-Touch Cube** - Stubbed interface only (Bluetooth, future feature)

## Template Generation

Two template engine implementations:

1. **Jinja Templates**: For raw ZPL/EPL2 text-based printer commands with Jinja2 templating.
2. **Image Templates**: PIL/Pillow-based rendering with visual elements:
   - Text with word wrapping, auto-scaling, and circle-aware layout
   - QR codes (via `qrcode` library)
   - DataMatrix barcodes (via `pylibdmtx`)
   - Rectangle and circle label shapes
   - Custom fonts (system, bundled, or Google Fonts)
   - Automatic conversion to ZPL (`^GFA`) or EPL2 (`GW`) bitmap commands

## Template Specification

* Templates stored as YAML files.
* Each template is a Pydantic model containing:
  - `engine`: Template engine type (`jinja` or `image`)
  - Template content (Jinja string for jinja engine)
  - Elements list (for image engine: text, qrcode, datamatrix)
  - Field definitions (name, type, validation)
  - Label dimensions (width/height or diameter for circles)
  - Label shape (`rectangle` or `circle`)
  - DPI setting (default 203)
  - Label offset (for printer calibration)
  - Print darkness (0-30)
  - Supported printer type(s)
  - Default quantity (optional)
  - Font paths (optional custom font directories)

## Label Workflow

1. **Template Creation**: YAML file with template content, fields, dimensions, and supported printers.
2. **Label Request**: Via REST API (`POST /api/v1/print/<template>`) or web form (FastUI).
3. **Queue**: Label enters in-memory queue. Printed when printer online. Dropped after 5 minutes (configurable). Queue is lost on restart.
4. **Response**: API returns different status codes for "printed" vs "queued awaiting printer".

## Printer Interface

* Each printer runs its own async task/thread.
* Connection types:
  - TCP (IP:port) for network-connected Zebra printers
  - Serial for USB/serial Zebra printers
  - Home Assistant integration (uses `zebra_printer` custom component)
  - Bluetooth for P-Touch (future, stubbed only)
* Web UI shows printer status indicator (online/offline).
* Cached status with configurable TTL to avoid blocking page renders.

## Web Interface

* Built with **FastUI** (Pydantic-native, auto-generates forms from models).
* Simple form-based UI for manual label entry.
* Printer status indicators.
* Template refresh button.
* No separate frontend JS application.

## CLI Tools

* `labelable-render`: Preview image templates as PNG without a printer
  - Supports field values via `-d key=value` or `--json file.json`
  - Can output ZPL/EPL2 format with `--format`
  - Auto-downloads missing Google Fonts with `--download-fonts`

## Implementation Requirements

* **Stack**: Python 3.12+, Pydantic, FastAPI, FastUI, uv, just
* **Type Checking**: basedpyright in standard mode
* **Tests**: pytest with coverage tracking
* **Scope**: Home project, no HA/enterprise features needed.
* **Documentation**: Maintain CLAUDE.md, include good README for GitHub.
* **Deployment**: Home Assistant add-on (include add-on config files and Dockerfile).
* **Clarification**: Ask before implementing if unsure.
