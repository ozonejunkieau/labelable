"""Bridge daemon for remote P-Touch USB printers.

Runs on a machine with a USB P-Touch printer attached and polls a remote
Labelable server for print jobs. All communication is outbound - no ports
need to be opened on the bridge machine.

Usage:
    labelable-bridge --url http://labelable-host:7979 --name my-ptouch
"""

import argparse
import asyncio
import logging
import sys

import aiohttp

from labelable.models.printer import HealthcheckConfig, PrinterConfig, PrinterType, USBConnection
from labelable.printers.ptouch import PTouchPrinter
from labelable.printers.ptouch_protocol import (
    CMD_STATUS_REQUEST,
    STATUS_RESPONSE_LENGTH,
    Error1,
    parse_status,
)

logger = logging.getLogger("labelable.bridge")

# How often to poll for jobs and report status (seconds)
POLL_INTERVAL = 2.0
STATUS_INTERVAL = 15.0


class BridgeDaemon:
    """Bridge daemon that polls Labelable for print jobs and relays them to USB."""

    def __init__(
        self,
        printer: PTouchPrinter,
        serial_number: str,
        labelable_url: str,
        name: str,
    ) -> None:
        self.printer = printer
        self.serial_number = serial_number
        self.labelable_url = labelable_url.rstrip("/")
        self.name = name
        self._registered_name: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._needs_reregister: bool = False

    @property
    def _api_base(self) -> str:
        return f"{self.labelable_url}/api/v1/bridge/{self._registered_name}"

    async def register(self) -> bool:
        """Register with the Labelable server."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        payload = {
            "serial_number": self.serial_number,
            "printer_name": self.name,
        }

        try:
            async with self._session.post(
                f"{self.labelable_url}/api/v1/bridge/register",
                json=payload,
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    self._registered_name = body.get("printer_name")
                    logger.info(f"Registered with Labelable as '{self._registered_name}'")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Registration failed (HTTP {resp.status}): {text}")
                    return False
        except Exception as e:
            logger.warning(f"Registration failed: {e}")
            return False

    async def report_status(self) -> None:
        """Query local USB printer status and report to Labelable."""
        if not self._session or not self._registered_name:
            return

        online = False
        model_info = None
        tape_width_mm = None
        media_kind = None
        tape_colour = None
        text_colour = None
        low_battery = None
        errors: list[str] = []

        try:
            await self.printer._send(CMD_STATUS_REQUEST)
            response = await self.printer._recv(size=STATUS_RESPONSE_LENGTH, timeout=3.0)

            if len(response) == STATUS_RESPONSE_LENGTH:
                status = parse_status(response)
                online = not status.has_errors
                kind = status.media_kind.name.replace("_", " ").title()
                model_info = f"P-Touch ({status.media_width_mm}mm {kind})"
                tape_width_mm = status.media_width_mm
                media_kind = status.media_kind.name.replace("_", " ").title()
                tape_colour = status.tape_colour
                text_colour = status.text_colour
                low_battery = bool(Error1.WEAK_BATTERY & status.error1)
                errors = status.error_descriptions

                if status.has_errors:
                    logger.warning(f"Printer errors: {', '.join(errors)}")
        except Exception as e:
            logger.warning(f"USB status check failed: {e}")

        try:
            async with self._session.post(
                f"{self._api_base}/status",
                json={
                    "online": online,
                    "model_info": model_info,
                    "tape_width_mm": tape_width_mm,
                    "media_kind": media_kind,
                    "tape_colour": tape_colour,
                    "text_colour": text_colour,
                    "low_battery": low_battery,
                    "errors": errors,
                },
            ) as resp:
                if resp.status == 404:
                    logger.warning("Server returned 404 for status — will re-register")
                    self._needs_reregister = True
                elif resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Status report failed (HTTP {resp.status}): {text}")
        except Exception as e:
            logger.warning(f"Status report failed: {e}")

    async def poll_and_print(self) -> None:
        """Poll for a pending job, print it, report result."""
        if not self._session or not self._registered_name:
            return

        try:
            async with self._session.get(f"{self._api_base}/job") as resp:
                if resp.status == 204:
                    return  # No pending job
                if resp.status == 404:
                    logger.warning("Server returned 404 for job poll — will re-register")
                    self._needs_reregister = True
                    return
                if resp.status != 200:
                    return

                data = await resp.read()
                if not data:
                    return
        except Exception as e:
            logger.warning(f"Job poll failed: {e}")
            return

        # Got a job - send to USB printer
        logger.info(f"Received print job ({len(data)} bytes)")
        ok = False
        error = None

        try:
            if not self.printer.is_connected:
                await self.printer.connect()
            await self.printer.print_raw(data)
            ok = True
            logger.info("Print job completed")
        except Exception as e:
            error = str(e)
            logger.error(f"Print failed: {e}")

        # Report result
        try:
            async with self._session.post(
                f"{self._api_base}/result",
                json={"ok": ok, "error": error},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Result report failed (HTTP {resp.status})")
        except Exception as e:
            logger.warning(f"Result report failed: {e}")

    async def run_loop(self) -> None:
        """Main polling loop."""
        status_counter = 0
        status_every = max(1, int(STATUS_INTERVAL / POLL_INTERVAL))

        while True:
            # Re-register if the server lost our printer (e.g. server restart)
            if self._needs_reregister:
                self._needs_reregister = False
                logger.info("Re-registering with Labelable...")
                if await self.register():
                    await self.report_status()
                    status_counter = 0
                else:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

            # Report status periodically
            status_counter += 1
            if status_counter >= status_every:
                await self.report_status()
                status_counter = 0

            # Poll for jobs
            await self.poll_and_print()

            await asyncio.sleep(POLL_INTERVAL)

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self._session:
            await self._session.close()
        await self.printer.disconnect()


def _get_usb_serial(vid: int, pid: int) -> str | None:
    """Get the USB serial number of the printer, or None if unavailable."""
    import usb.core  # type: ignore[import-untyped]

    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise ConnectionError(f"USB device {vid:04x}:{pid:04x} not found")
    serial = dev.serial_number  # type: ignore[union-attr]
    if not serial:
        return None
    return serial


async def _run(args: argparse.Namespace) -> int:
    """Main async entry point."""
    vid = args.vid
    pid = args.pid

    # Find USB printer and get serial number
    try:
        usb_serial = _get_usb_serial(vid, pid)
        if usb_serial:
            logger.info(f"USB printer found: serial={usb_serial}")
    except ConnectionError as e:
        logger.error(str(e))
        return 1

    serial_number = usb_serial or args.serial
    if not serial_number:
        logger.error("USB device has no serial number. Use --serial to provide one manually.")
        return 1

    labelable_url: str = args.url

    # Create USB printer instance
    connection = USBConnection(vendor_id=vid, product_id=pid)
    config = PrinterConfig(
        name="bridge-local",
        type=PrinterType.PTOUCH,
        connection=connection,
        healthcheck=HealthcheckConfig(),
    )
    printer = PTouchPrinter(config)

    try:
        await printer.connect()
    except ConnectionError as e:
        logger.error(f"Failed to connect to USB printer: {e}")
        return 1

    daemon = BridgeDaemon(
        printer=printer,
        serial_number=serial_number,
        labelable_url=labelable_url,
        name=args.name,
    )

    # Register with Labelable (retry until successful)
    for attempt in range(10):
        if await daemon.register():
            break
        logger.warning(f"Registration attempt {attempt + 1} failed, retrying in 5s...")
        await asyncio.sleep(5)
    else:
        logger.error("Failed to register with Labelable after 10 attempts")
        await daemon.cleanup()
        return 1

    # Report initial status
    await daemon.report_status()

    logger.info(f"Bridge daemon running (polling every {POLL_INTERVAL}s)")

    # Run polling loop until interrupted
    try:
        await daemon.run_loop()
    except asyncio.CancelledError:
        pass
    finally:
        await daemon.cleanup()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bridge daemon for remote P-Touch USB printers.",
        prog="labelable-bridge",
    )
    parser.add_argument(
        "--url",
        type=str,
        required=True,
        help="Labelable server URL (e.g. http://192.168.1.10:7979)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="ptouch-bridge",
        help="Printer name to register with Labelable (default: ptouch-bridge)",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help="Serial number override (used when USB device has no serial)",
    )
    parser.add_argument(
        "--vid",
        type=lambda x: int(x, 0),
        default=0x04F9,
        help="USB vendor ID (default: 0x04f9)",
    )
    parser.add_argument(
        "--pid",
        type=lambda x: int(x, 0),
        default=0x20AF,
        help="USB product ID (default: 0x20af)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("Shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
