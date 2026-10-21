"""Entry point for running Labelable as a module."""

import logging
import os
import sys
from pathlib import Path

from labelable._extras import missing_extra

try:
    import uvicorn

    from labelable.config import load_config, settings
except ImportError as e:
    missing_extra("server", e)


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


async def serve_dual(primary_server: uvicorn.Server, secondary_server: uvicorn.Server) -> None:
    """Serve one app on two listeners, running its lifespan exactly once.

    Both configs import the same ``labelable.app:app``, so they share the
    module-level state the lifespan builds (printers, queue workers, Cloudflare
    consumer). Letting each server run the lifespan would build all of that
    twice: two workers per printer contending for one TCP or USB connection,
    and a second Cloudflare consumer bound to a stale template set. So only
    ``primary`` runs it, and ``secondary`` starts once ``primary`` is up.

    When either server stops, the other is told to stop too.
    """
    import asyncio

    secondary_server.config.lifespan = "off"

    primary_task = asyncio.create_task(primary_server.serve())
    while not primary_server.started:
        if primary_task.done():
            # Startup failed (lifespan error, port in use); nothing to pair with
            await primary_task
            return
        await asyncio.sleep(0.05)

    secondary_task = asyncio.create_task(secondary_server.serve())
    done, pending = await asyncio.wait({primary_task, secondary_task}, return_when=asyncio.FIRST_COMPLETED)
    primary_server.should_exit = True
    secondary_server.should_exit = True
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


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
        log.info(f"HTTP on port {settings.port} (ingress)")
        log.info(f"HTTPS on port {settings.ssl_port} (external)")
        asyncio.run(serve_dual(uvicorn.Server(http_config), uvicorn.Server(https_config)))
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
