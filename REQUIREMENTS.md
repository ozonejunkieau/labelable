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
* Templates defined in separate YAML files with Jinja template strings and metadata.
* No authentication required (internal/trusted network use only).

## Label Printer Interface

* An agnostic interface to label printers, at minimum checking if the printer is online or offline.
* Known printers that will be used:
  - **Zebra ZPL printers** - Full support (TCP/serial)
  - **Zebra EPL2 printers** - Full support (TCP/serial)
  - **Brother P-Touch Cube** - Stubbed interface only (Bluetooth, future feature)

## Template Generation

Two template engine implementations:

1. **Jinja Templates** (primary): For ZPL/EPL2 text-based printer commands.
2. **Bitmap/Image Templates** (for P-Touch): Python-based templates using PIL/Pillow to generate bitmap images with positioned text. This is required because P-Touch printers need bitmap data streamed to them.

## Template Specification

* Templates stored as YAML files with Jinja template strings.
* Each template is a Pydantic model containing:
  - Template content (Jinja string or Python bitmap generator reference)
  - Field definitions (name, type, validation)
  - Label dimensions (width, height) - required per template
  - Supported printer type(s)
  - Default quantity (optional)
* Some printers (notably P-Touch) may allow querying label dimensions - handle gracefully.

## Label Workflow

1. **Template Creation**: YAML file with template content, fields, dimensions, and supported printers.
2. **Label Request**: Via REST API (`POST /api/v1/label/<template>`) or web form (FastUI).
3. **Queue**: Label enters in-memory queue. Printed when printer online. Dropped after 5 minutes (configurable). Queue is lost on restart.
4. **Response**: API returns different status codes for "printed" vs "queued awaiting printer".

## Printer Interface

* Each printer runs its own async task/thread.
* Connection types:
  - TCP (IP:port) for network-connected Zebra printers
  - Serial for USB/serial Zebra printers
  - Bluetooth for P-Touch (future, stubbed only)
* Web UI shows printer status indicator (online/offline).

## Web Interface

* Built with **FastUI** (Pydantic-native, auto-generates forms from models).
* Simple form-based UI for manual label entry.
* Printer status indicators.
* No separate frontend JS application.

## Implementation Requirements

* **Stack**: Python, Pydantic, FastAPI, FastUI, uv, just
* **Tests**: Should exist but don't need to be exhaustive.
* **Scope**: Home project, no HA/enterprise features needed.
* **Documentation**: Maintain CLAUDE.md, include good README for GitHub.
* **Deployment**: Home Assistant add-on (include add-on config files and Dockerfile).
* **Clarification**: Ask before implementing if unsure.