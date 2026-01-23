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
    Look for sensor.*_language entities which are unique to zebra_printer.
    The entity name gives us the device name, and the state gives us the protocol.
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.info("SUPERVISOR_TOKEN not set, skipping HA printer discovery")
        return []

    headers = {"Authorization": f"Bearer {supervisor_token}"}
    base_url = "http://supervisor/core/api"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # Get entity states to find zebra_printer sensors
            logger.debug(f"Querying HA states: {base_url}/states")
            async with session.get(f"{base_url}/states") as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Failed to query HA states: {resp.status} - {text}")
                    return []
                states = await resp.json()
                logger.debug(f"States API returned {len(states)} entities")

        printers = []
        seen_devices = set()

        for state in states:
            entity_id = state.get("entity_id", "")

            # Look for language sensors from zebra_printer integration
            # Format: sensor.{device_name}_language
            if not entity_id.startswith("sensor.") or not entity_id.endswith("_language"):
                continue

            # Extract device name from entity ID
            # sensor.my_printer_language -> my_printer
            device_name = entity_id[7:-9]  # Remove "sensor." prefix and "_language" suffix
            if device_name in seen_devices:
                continue
            seen_devices.add(device_name)

            # Get printer type from language sensor value
            language = (state.get("state") or "").upper()
            if language == "EPL2":
                printer_type = PrinterType.EPL2
            elif language == "ZPL":
                printer_type = PrinterType.ZPL
            else:
                logger.warning(f"Unknown printer language '{language}' for {device_name}, defaulting to ZPL")
                printer_type = PrinterType.ZPL

            # Use the device name as the device_id for service calls
            # The HA service will need to look up the actual device from this
            printers.append(
                PrinterConfig(
                    name=f"ha-{device_name}",
                    type=printer_type,
                    connection=HAConnection(device_id=device_name),
                )
            )
            logger.info(f"Discovered HA printer: {device_name} (type={printer_type})")

        if not printers:
            logger.info("No zebra_printer devices found in HA states")
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
