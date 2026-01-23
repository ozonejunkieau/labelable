"""Configuration management for Labelable."""

import logging
import os
from pathlib import Path

import aiohttp
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from labelable.models.printer import HAConnection, PrinterConfig, PrinterType
from labelable.models.template import TemplateConfig

logger = logging.getLogger(__name__)


class AppConfig(BaseModel):
    """Application configuration loaded from config.yaml."""

    queue_timeout_seconds: int = 300
    templates_dir: Path = Path("./templates")
    printers: list[PrinterConfig] = Field(default_factory=list)
    # User mapping: HA user ID -> display name
    user_mapping: dict[str, str] = Field(default_factory=dict)
    default_user: str = ""
    # API key for external access (optional, if not set API is open)
    api_key: str | None = None


class Settings(BaseSettings):
    """Environment-based settings."""

    model_config = SettingsConfigDict(
        env_prefix="LABELABLE_",
        env_file=".env",
        extra="ignore",
    )

    config_file: Path = Path("config.yaml")
    host: str = "0.0.0.0"
    port: int = 7979
    debug: bool = False
    show_user_debug: bool = True  # Show HA user ID on home page for debugging user_mapping


def load_config(config_path: Path) -> AppConfig:
    """Load application configuration from YAML file."""
    if not config_path.exists():
        return AppConfig()

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    # Handle None values for list/dict fields (YAML returns None for empty keys)
    if data.get("printers") is None:
        data["printers"] = []
    if data.get("user_mapping") is None:
        data["user_mapping"] = {}

    return AppConfig.model_validate(data)


def load_templates(templates_dir: Path) -> dict[str, TemplateConfig]:
    """Load all template configurations from the templates directory."""
    templates: dict[str, TemplateConfig] = {}

    if not templates_dir.exists():
        return templates

    for template_file in templates_dir.glob("*.yaml"):
        # Skip example/reference templates (files starting with underscore)
        if template_file.name.startswith("_"):
            continue

        try:
            with open(template_file) as f:
                data = yaml.safe_load(f)
            if data:
                template = TemplateConfig.model_validate(data)
                templates[template.name] = template
        except Exception as e:
            # Log but don't fail on individual template errors
            print(f"Warning: Failed to load template {template_file}: {e}")

    return templates


async def discover_ha_printers() -> list[PrinterConfig]:
    """Discover printers from HA zebra_printer integration.

    This function queries the Home Assistant API to find any printers
    managed by the zebra_printer integration and returns them as
    PrinterConfig objects with HA connections.

    Detection strategy:
    1. Query device registry for zebra_printer devices
    2. Query entity states to get language (printer type)
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.info("SUPERVISOR_TOKEN not set, skipping HA printer discovery")
        return []

    headers = {"Authorization": f"Bearer {supervisor_token}"}
    base_url = "http://supervisor/core/api"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # Get device registry to find zebra_printer devices
            logger.debug(f"Querying device registry: {base_url}/config/device_registry")
            async with session.get(f"{base_url}/config/device_registry") as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Failed to query device registry: {resp.status} - {text}")
                    return []
                devices = await resp.json()
                logger.debug(f"Device registry returned {len(devices)} devices")

            # Get entity states to determine printer type
            async with session.get(f"{base_url}/states") as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to query HA states: {resp.status}")
                    return []
                states = await resp.json()
                logger.debug(f"States API returned {len(states)} entities")

        # Build entity_id -> state lookup
        state_lookup = {s["entity_id"]: s for s in states}

        printers = []
        for device in devices:
            # Check if this device is from zebra_printer integration
            identifiers = device.get("identifiers", [])
            is_zebra = any(
                isinstance(ident, (list, tuple)) and len(ident) >= 1 and ident[0] == "zebra_printer"
                for ident in identifiers
            )
            if not is_zebra:
                continue

            device_id = device.get("id")
            device_name = device.get("name_by_user") or device.get("name") or device_id
            logger.debug(f"Found zebra_printer device: {device_name} (id={device_id})")

            # Make a safe name for the printer
            safe_name = device_name.lower().replace(" ", "_").replace("-", "_")

            # Find language sensor to determine printer type
            # Entity ID format: sensor.{safe_device_name}_language
            language_entity = None
            for entity_id in state_lookup:
                if entity_id.endswith("_language") and entity_id.startswith("sensor."):
                    # Check if this entity belongs to this device by checking name match
                    if safe_name in entity_id or device_name.lower().replace(" ", "_") in entity_id:
                        language_entity = state_lookup[entity_id]
                        logger.debug(f"Found language sensor: {entity_id} = {language_entity.get('state')}")
                        break

            # Determine printer type from language sensor (defaults to ZPL)
            printer_type = PrinterType.ZPL
            if language_entity:
                language = (language_entity.get("state") or "").upper()
                if language == "EPL2":
                    printer_type = PrinterType.EPL2

            printers.append(
                PrinterConfig(
                    name=f"ha-{safe_name}",
                    type=printer_type,
                    connection=HAConnection(device_id=device_id),
                )
            )
            logger.info(f"Discovered HA printer: {device_name} (id={device_id}, type={printer_type})")

        if not printers:
            logger.info("No zebra_printer devices found in HA device registry")
        else:
            logger.info(f"Discovered {len(printers)} printer(s) from HA integration")

        return printers

    except Exception as e:
        logger.warning(f"Error during HA printer discovery: {e}", exc_info=True)
        return []


async def load_config_async(config_path: Path) -> AppConfig:
    """Load application configuration with HA auto-discovery.

    This is an async version of load_config that also attempts to
    discover printers from the Home Assistant zebra_printer integration
    if no printers are configured.
    """
    config = load_config(config_path)

    # If no printers configured, try HA auto-discovery
    if not config.printers:
        config.printers = await discover_ha_printers()

    return config


# Global settings instance
settings = Settings()
