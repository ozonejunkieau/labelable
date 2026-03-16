"""Entry point for running Labelable as a module."""

import logging
import os
import sys
from pathlib import Path

import uvicorn

from labelable.config import load_config, settings


def _setup_macos_library_path() -> None:
    """Set up library path for macOS Homebrew installations.

    pylibdmtx requires libdmtx to be findable. On macOS with Homebrew,
    the library is installed to /opt/homebrew/lib (Apple Silicon) or
    /usr/local/lib (Intel), but these aren't in the default search path.
    """
    if sys.platform != "darwin":
        return

    if os.environ.get("DYLD_LIBRARY_PATH"):
        return  # Already set by user

    # Check common Homebrew library locations
    homebrew_paths = [
        Path("/opt/homebrew/lib"),  # Apple Silicon
        Path("/usr/local/lib"),  # Intel
    ]

    for lib_path in homebrew_paths:
        if (lib_path / "libdmtx.dylib").exists():
            os.environ["DYLD_LIBRARY_PATH"] = str(lib_path)
            return


def main() -> int:
    """Run the Labelable server."""
    # Set up macOS library path for pylibdmtx
    _setup_macos_library_path()

    log = logging.getLogger(__name__)

    # Configure logging
    log_level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    has_ssl = bool(settings.ssl_certfile and settings.ssl_keyfile)
    dual_mode = has_ssl and os.environ.get("LABELABLE_DUAL_HTTP") == "1"

    if dual_mode:
        # Dual mode: HTTP on main port + HTTPS on ssl_port (for HA add-on)
        import asyncio

        async def _run_dual() -> None:
            http_config = uvicorn.Config(
                "labelable.app:app",
                host=settings.host,
                port=settings.port,
            )
            https_config = uvicorn.Config(
                "labelable.app:app",
                host=settings.host,
                port=settings.ssl_port,
                ssl_certfile=str(settings.ssl_certfile),
                ssl_keyfile=str(settings.ssl_keyfile),
            )
            http_server = uvicorn.Server(http_config)
            https_server = uvicorn.Server(https_config)

            log.info(f"HTTP on port {settings.port} (ingress)")
            log.info(f"HTTPS on port {settings.ssl_port} (external)")

            await asyncio.gather(
                http_server.serve(),
                https_server.serve(),
            )

        asyncio.run(_run_dual())
    else:
        # Single server mode
        uvicorn_kwargs: dict = {
            "host": settings.host,
            "port": settings.port,
            "reload": settings.debug,
        }

        if has_ssl:
            uvicorn_kwargs["ssl_certfile"] = str(settings.ssl_certfile)
            uvicorn_kwargs["ssl_keyfile"] = str(settings.ssl_keyfile)
            log.info(f"HTTPS enabled (cert={settings.ssl_certfile}, key={settings.ssl_keyfile})")

        # In debug mode, watch templates directory for changes
        if settings.debug:
            config = load_config(settings.config_file)
            templates_path = Path(config.templates_dir)
            if not templates_path.is_absolute():
                templates_path = settings.config_file.parent / templates_path
            if templates_path.exists():
                uvicorn_kwargs["reload_dirs"] = [str(templates_path)]
                uvicorn_kwargs["reload_includes"] = ["*.yaml"]

        uvicorn.run("labelable.app:app", **uvicorn_kwargs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
