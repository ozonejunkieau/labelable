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

    # Configure logging
    log_level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Build uvicorn config
    uvicorn_kwargs: dict = {
        "host": settings.host,
        "port": settings.port,
        "reload": settings.debug,
    }

    # In debug mode, watch templates directory for changes
    if settings.debug:
        config = load_config(settings.config_file)
        templates_path = Path(config.templates_dir)
        if not templates_path.is_absolute():
            templates_path = settings.config_file.parent / templates_path
        if templates_path.exists():
            uvicorn_kwargs["reload_dirs"] = [str(templates_path)]
            uvicorn_kwargs["reload_includes"] = ["*.yaml"]

    # Run the server
    uvicorn.run("labelable.app:app", **uvicorn_kwargs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
