"""Pytest configuration and fixtures."""

import os
import sys
from pathlib import Path

import pytest


def _setup_macos_library_path() -> None:
    """Set up library path for macOS Homebrew installations."""
    if sys.platform != "darwin":
        return

    if os.environ.get("DYLD_LIBRARY_PATH"):
        return

    homebrew_paths = [
        Path("/opt/homebrew/lib"),  # Apple Silicon
        Path("/usr/local/lib"),  # Intel
    ]

    for lib_path in homebrew_paths:
        if (lib_path / "libdmtx.dylib").exists():
            os.environ["DYLD_LIBRARY_PATH"] = str(lib_path)
            return


# Set up library path before any imports that might need it
_setup_macos_library_path()


@pytest.fixture
def anyio_backend():
    """Use asyncio for async tests."""
    return "asyncio"
