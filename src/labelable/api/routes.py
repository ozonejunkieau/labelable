"""REST API routes for Labelable."""

import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from labelable.models.job import JobStatus, PrintJob
from labelable.models.printer import PrinterType
from labelable.models.template import EngineType, TemplateConfig, TemplateField

router = APIRouter(prefix="/api/v1", tags=["api"])

# These will be set by the app during startup
_app_state: dict[str, Any] = {}


def set_app_state(
    printers: dict,
    templates: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any = None,
    api_key: str | None = None,
) -> None:
    """Set application state references for the routes."""
    _app_state["printers"] = printers
    _app_state["templates"] = templates
    _app_state["queue"] = queue
    _app_state["jinja_engine"] = jinja_engine
    _app_state["image_engine"] = image_engine
    _app_state["api_key"] = api_key


async def verify_api_key(request: Request) -> None:
    """Verify API key if configured.

    API key can be provided via:
    - X-API-Key header
    - Authorization: Bearer <key> header
    - api_key query parameter

    If no API key is configured, all requests are allowed.
    Requests from Home Assistant (with X-Ingress-Path header) bypass auth.
    """
    configured_key = _app_state.get("api_key")

    # No API key configured = open access
    if not configured_key:
        return

    # Requests via HA Ingress are already authenticated by HA
    if request.headers.get("X-Ingress-Path"):
        return

    # Check various auth methods
    provided_key = None

    # X-API-Key header
    if "X-API-Key" in request.headers:
        provided_key = request.headers["X-API-Key"]
    # Authorization: Bearer header
    elif "Authorization" in request.headers:
        auth = request.headers["Authorization"]
        if auth.startswith("Bearer "):
            provided_key = auth[7:]
    # Query parameter (less secure, but convenient for testing)
    elif "api_key" in request.query_params:
        provided_key = request.query_params["api_key"]

    if not provided_key or not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Response models


class PrinterStatus(BaseModel):
    """Printer status response."""

    name: str
    type: PrinterType
    online: bool
    queue_size: int
    last_checked: datetime | None = None


class TemplateInfo(BaseModel):
    """Template information response."""

    name: str
    description: str
    width_mm: float
    height_mm: float
    supported_printers: list[str]  # Printer names
    fields: list[TemplateField]


class PrintRequest(BaseModel):
    """Print request body."""

    printer: str | None = None
    quantity: int = 1
    data: dict[str, Any] = {}


class PrintResponse(BaseModel):
    """Print response."""

    job_id: str
    status: JobStatus
    message: str


# Endpoints


@router.get("/printers", response_model=list[PrinterStatus])
async def list_printers() -> list[PrinterStatus]:
    """List all configured printers with their status."""
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")

    result = []
    for name, printer in printers.items():
        online = await printer.is_online()
        queue_size = queue.get_queue_size(name) if queue else 0
        result.append(
            PrinterStatus(
                name=name,
                type=printer.config.type,
                online=online,
                queue_size=queue_size,
                last_checked=printer.last_checked,
            )
        )
    return result


@router.get("/printers/{name}", response_model=PrinterStatus)
async def get_printer(name: str) -> PrinterStatus:
    """Get status of a specific printer."""
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")

    if name not in printers:
        raise HTTPException(status_code=404, detail=f"Printer '{name}' not found")

    printer = printers[name]
    online = await printer.is_online()
    queue_size = queue.get_queue_size(name) if queue else 0

    return PrinterStatus(
        name=name,
        type=printer.config.type,
        online=online,
        queue_size=queue_size,
        last_checked=printer.last_checked,
    )


@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates() -> list[TemplateInfo]:
    """List all available templates."""
    templates = _app_state.get("templates", {})
    return [_template_to_info(t) for t in templates.values()]


@router.get("/templates/{name}", response_model=TemplateInfo)
async def get_template(name: str) -> TemplateInfo:
    """Get details of a specific template."""
    templates = _app_state.get("templates", {})

    if name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    return _template_to_info(templates[name])


@router.post(
    "/print/{template_name}",
    response_model=PrintResponse,
    responses={
        200: {"description": "Label printed successfully"},
        202: {"description": "Label queued for printing"},
        400: {"description": "Invalid request"},
        404: {"description": "Template or printer not found"},
    },
)
async def print_label(template_name: str, request: PrintRequest) -> PrintResponse:
    """Submit a label for printing.

    Returns 200 if printed immediately, 202 if queued.
    """
    templates = _app_state.get("templates", {})
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")
    jinja_engine = _app_state.get("jinja_engine")

    if queue is None:
        raise HTTPException(status_code=500, detail="Print queue not initialized")
    if jinja_engine is None:
        raise HTTPException(status_code=500, detail="Template engine not initialized")

    # Validate template
    if template_name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    template = templates[template_name]

    # Determine printer
    printer_name = request.printer
    if not printer_name:
        # Find first available printer that supports this template
        for name, _printer in printers.items():
            if name in template.supported_printers:
                printer_name = name
                break

    if not printer_name:
        raise HTTPException(
            status_code=400,
            detail="No printer specified and no compatible printer found",
        )

    if printer_name not in printers:
        raise HTTPException(status_code=404, detail=f"Printer '{printer_name}' not found")

    printer = printers[printer_name]

    # Validate printer supports template
    if printer_name not in template.supported_printers:
        raise HTTPException(
            status_code=400,
            detail=f"Printer '{printer_name}' not in template's supported_printers list",
        )

    # Add quantity to context for template use (e.g., "1 of {{ quantity }}")
    render_context = {**request.data, "quantity": request.quantity}

    # Render template using the appropriate engine
    try:
        if template.engine == EngineType.IMAGE:
            image_engine = _app_state.get("image_engine")
            if not image_engine:
                raise HTTPException(status_code=500, detail="Image engine not initialized")
            # Determine output format from printer type
            output_format = printer.config.type.value  # "zpl" or "epl2"
            rendered = image_engine.render(template, render_context, output_format=output_format)
        else:
            rendered = jinja_engine.render(template, render_context)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Template rendering failed: {e}") from e

    # Create print job
    job = PrintJob(
        template_name=template_name,
        printer_name=printer_name,
        data=request.data,
        quantity=request.quantity,
        rendered_content=rendered,
    )

    # Check if printer is online
    is_online = await printer.is_online()

    # Submit to queue
    await queue.submit(job)

    if is_online:
        return PrintResponse(
            job_id=str(job.id),
            status=JobStatus.PENDING,
            message="Label submitted for printing",
        )
    else:
        # Return 202 Accepted for queued jobs
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail=PrintResponse(
                job_id=str(job.id),
                status=JobStatus.PENDING,
                message=f"Label queued - printer '{printer_name}' is offline",
            ).model_dump(),
        )


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> PrintResponse:
    """Get the status of a print job."""
    queue = _app_state.get("queue")
    if queue is None:
        raise HTTPException(status_code=500, detail="Print queue not initialized")
    job = queue.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    message = job.error_message or f"Job status: {job.status}"
    return PrintResponse(job_id=job_id, status=job.status, message=message)


def _template_to_info(template: TemplateConfig) -> TemplateInfo:
    """Convert a TemplateConfig to TemplateInfo response."""
    return TemplateInfo(
        name=template.name,
        description=template.description,
        width_mm=template.dimensions.width_mm,
        height_mm=template.dimensions.height_mm,
        supported_printers=template.supported_printers,
        fields=template.fields,
    )
