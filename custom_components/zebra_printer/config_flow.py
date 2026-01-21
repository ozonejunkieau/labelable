"""Config flow for Zebra Printer integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_PROTOCOL,
    DEFAULT_PORT,
    DOMAIN,
    PROTOCOL_EPL2,
    PROTOCOL_ZPL,
    ZEBRA_OUI_PREFIXES,
)
from .protocol import EPL2Protocol, ZPLProtocol

_LOGGER = logging.getLogger(__name__)


async def detect_protocol(host: str, port: int) -> str | None:
    """Auto-detect printer protocol.

    Returns 'zpl', 'epl2', or None if detection fails.
    """
    # Try ZPL first (more common)
    zpl = ZPLProtocol(host, port)
    try:
        if await zpl.connect():
            if await zpl.probe():
                return PROTOCOL_ZPL
    finally:
        await zpl.disconnect()

    # Try EPL2
    epl2 = EPL2Protocol(host, port)
    try:
        if await epl2.connect():
            if await epl2.probe():
                return PROTOCOL_EPL2
    finally:
        await epl2.disconnect()

    return None


async def get_printer_info(
    host: str, port: int, protocol: str
) -> dict[str, Any] | None:
    """Get printer identification info."""
    if protocol == PROTOCOL_ZPL:
        proto = ZPLProtocol(host, port)
    else:
        proto = EPL2Protocol(host, port)

    try:
        if not await proto.connect():
            return None
        status = await proto.get_status()
        return {
            "model": status.model or "Unknown Zebra Printer",
            "firmware": status.firmware,
        }
    finally:
        await proto.disconnect()


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format."""
    mac_upper = mac.upper().replace("-", ":").replace(".", ":")
    # Normalize to colon-separated format
    if ":" not in mac_upper:
        # Convert AABBCCDDEEFF to AA:BB:CC:DD:EE:FF
        mac_upper = ":".join(mac_upper[i : i + 2] for i in range(0, 12, 2))
    return mac_upper


def is_zebra_mac(mac: str) -> bool:
    """Check if MAC address belongs to Zebra."""
    mac_normalized = normalize_mac(mac)

    for prefix in ZEBRA_OUI_PREFIXES:
        if mac_normalized.startswith(prefix):
            return True
    return False


class ZebraPrinterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zebra Printer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_mac: str | None = None
        self._discovered_protocol: str | None = None
        self._discovered_info: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user-initiated configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            # Check for duplicates
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

            # Try to detect protocol
            protocol = await detect_protocol(host, port)

            if protocol is None:
                errors["base"] = "cannot_connect"
            else:
                # Get printer info for the title
                info = await get_printer_info(host, port, protocol)
                title = info["model"] if info else f"Zebra Printer ({host})"

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_PROTOCOL: protocol,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                }
            ),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:
        """Handle DHCP discovery."""
        mac = normalize_mac(discovery_info.macaddress)
        host = discovery_info.ip

        _LOGGER.debug("DHCP discovery: host=%s, mac=%s", host, mac)

        # Verify this is a Zebra device
        if not is_zebra_mac(mac):
            return self.async_abort(reason="not_zebra_device")

        # Check for existing entries (use normalized MAC as unique ID)
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        # Try to detect protocol
        protocol = await detect_protocol(host, DEFAULT_PORT)
        if protocol is None:
            # Printer might be busy, allow manual setup
            return self.async_abort(reason="cannot_connect")

        # Get printer info
        info = await get_printer_info(host, DEFAULT_PORT, protocol)
        model = info["model"] if info else "Zebra Printer"

        self._discovered_host = host
        self._discovered_mac = mac
        self._discovered_protocol = protocol
        self._discovered_info = info

        # Set title for discovery notification (top line in HA UI)
        self.context["title_placeholders"] = {
            "name": f"{model} ({host}:{DEFAULT_PORT})"
        }

        # Show confirmation dialog
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm DHCP discovery."""
        if user_input is not None:
            title = (
                self._discovered_info["model"]
                if self._discovered_info
                else f"Zebra Printer ({self._discovered_host})"
            )

            return self.async_create_entry(
                title=title,
                data={
                    CONF_HOST: self._discovered_host,
                    CONF_PORT: DEFAULT_PORT,
                    CONF_PROTOCOL: self._discovered_protocol,
                },
            )

        model = (
            self._discovered_info["model"]
            if self._discovered_info
            else "Zebra Printer"
        )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="dhcp_confirm",
            description_placeholders={
                "model": model,
                "host": self._discovered_host,
            },
        )
