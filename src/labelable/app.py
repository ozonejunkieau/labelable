"""FastAPI application factory for Labelable."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from labelable.api import routes as api_routes
from labelable.api import ui as ui_routes
from labelable.config import AppConfig, load_config_async, load_templates, settings
from labelable.printers import BasePrinter, create_printer
from labelable.queue import PrintQueue
from labelable.templates.image_engine import ImageTemplateEngine
from labelable.templates.jinja_engine import JinjaTemplateEngine


class IngressPathMiddleware:
    """Middleware to handle Home Assistant Ingress path prefix.

    Sets the ASGI root_path from X-Ingress-Path header so FastAPI
    and FastUI properly handle the ingress proxy prefix.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            ingress_path = headers.get(b"x-ingress-path", b"").decode()
            if ingress_path:
                scope = scope.copy()
                scope["root_path"] = ingress_path.rstrip("/")
        await self.app(scope, receive, send)


logger = logging.getLogger(__name__)

# Application state
_printers: dict[str, BasePrinter] = {}
_templates: dict = {}
_queue: PrintQueue | None = None
_jinja_engine: JinjaTemplateEngine | None = None
_image_engine: ImageTemplateEngine | None = None
_config: AppConfig | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global _printers, _templates, _queue, _jinja_engine, _image_engine, _config

    # Load configuration (with optional HA auto-discovery)
    logger.info(f"Loading configuration from {settings.config_file}")
    _config = await load_config_async(settings.config_file)

    # Resolve fonts directory
    fonts_path = Path(_config.fonts_dir)
    if not fonts_path.is_absolute():
        fonts_path = settings.config_file.parent / fonts_path
    if _config.download_google_fonts:
        logger.info(f"Google Fonts downloading enabled, fonts dir: {fonts_path}")

    # Load templates
    templates_path = Path(_config.templates_dir)
    if not templates_path.is_absolute():
        templates_path = settings.config_file.parent / templates_path
    logger.info(f"Loading templates from {templates_path}")
    template_result = load_templates(
        templates_path,
        fonts_dir=fonts_path,
        download_google_fonts=_config.download_google_fonts,
    )
    _templates = template_result.templates
    _template_warnings = template_result.warnings
    logger.info(f"Loaded {len(_templates)} templates")
    if _template_warnings:
        for warning in _template_warnings:
            logger.warning(warning)

    # Initialize template engines
    _jinja_engine = JinjaTemplateEngine()
    _image_engine = ImageTemplateEngine(custom_font_paths=[str(fonts_path)] if fonts_path.exists() else None)

    # Initialize print queue
    _queue = PrintQueue(timeout_seconds=_config.queue_timeout_seconds)

    # Initialize printers
    for printer_config in _config.printers:
        if not printer_config.enabled:
            logger.info(f"Skipping disabled printer: {printer_config.name}")
            continue

        try:
            printer = create_printer(printer_config)
            _printers[printer_config.name] = printer
            logger.info(f"Initialized printer: {printer_config.name} ({printer_config.type})")

            # Start queue worker for this printer
            await _queue.start_worker(printer)
        except Exception as e:
            logger.error(f"Failed to initialize printer {printer_config.name}: {e}")

    # Set state for routes
    api_routes.set_app_state(_printers, _templates, _queue, _jinja_engine, _image_engine, api_key=_config.api_key)
    ui_routes.set_app_state(
        _printers,
        _templates,
        _queue,
        _jinja_engine,
        _image_engine,
        user_mapping=_config.user_mapping,
        default_user=_config.default_user,
        templates_path=templates_path,
        template_warnings=_template_warnings,
    )

    logger.info("Labelable startup complete")

    yield

    # Shutdown
    logger.info("Labelable shutting down")

    # Stop queue workers
    if _queue:
        await _queue.stop_all()

    # Disconnect printers
    for printer in _printers.values():
        try:
            await printer.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting printer {printer.name}: {e}")

    logger.info("Labelable shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Labelable",
        description="A general purpose label printing API and UI",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add middleware for HA Ingress path handling
    app.add_middleware(IngressPathMiddleware)

    # Include routers with API key auth for API routes
    app.include_router(
        api_routes.router,
        dependencies=[Depends(api_routes.verify_api_key)],
    )
    app.include_router(ui_routes.router)

    return app


# Default app instance for uvicorn
app = create_app()
