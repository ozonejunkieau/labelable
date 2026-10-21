"""Explain a missing optional extra instead of failing with a bare ImportError."""

from typing import NoReturn

# Top-level modules each extra installs
_EXTRA_MODULES = {
    "bridge": {"aiohttp", "serial", "usb"},
    "server": {"aiohttp", "serial", "usb", "fastapi", "fastui", "uvicorn", "pydantic_settings", "multipart"},
}


def missing_extra(extra: str, error: ImportError) -> NoReturn:
    """Exit with an install hint if ``error`` is a module from ``extra``, else re-raise it."""
    module = (error.name or "").partition(".")[0]
    if module not in _EXTRA_MODULES[extra]:
        raise error
    raise SystemExit(
        f"This command needs the labelable[{extra}] extra (module '{module}' is not installed).\n"
        f"Install it with: uv tool install 'labelable[{extra}] @ git+https://github.com/ozonejunkieau/labelable'"
    )
