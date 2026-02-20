"""MCP server exposing Labelable tools for AI assistants."""

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from labelable.api.template_crud import TemplateCRUDError, create_template_on_disk, update_template_on_disk
from labelable.models.job import PrintJob
from labelable.models.template import EngineType, TemplateConfig

logger = logging.getLogger(__name__)

# Module-level state (set by the app during startup)
_app_state: dict[str, Any] = {}


def set_app_state(
    printers: dict,
    templates: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any = None,
    api_key: str | None = None,
    templates_path: Path | None = None,
) -> None:
    """Set application state references for the MCP tools."""
    _app_state["printers"] = printers
    _app_state["templates"] = templates
    _app_state["queue"] = queue
    _app_state["jinja_engine"] = jinja_engine
    _app_state["image_engine"] = image_engine
    _app_state["api_key"] = api_key
    _app_state["templates_path"] = templates_path


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with all tools."""
    mcp = FastMCP("Labelable", stateless_http=True)

    @mcp.tool()
    async def list_printers() -> str:
        """List all configured printers with their status.

        Returns a JSON array of printer objects with name, type, online status, and queue size.
        """
        printers = _app_state.get("printers", {})
        queue = _app_state.get("queue")

        result = []
        for name, printer in printers.items():
            online = await printer.is_online()
            queue_size = queue.get_queue_size(name) if queue else 0
            result.append(
                {
                    "name": name,
                    "type": printer.config.type.value,
                    "online": online,
                    "queue_size": queue_size,
                }
            )
        return json.dumps(result)

    @mcp.tool()
    async def list_templates() -> str:
        """List all available label templates.

        Returns a JSON array of template summaries with name, description, dimensions,
        supported printers, and field definitions.
        """
        templates = _app_state.get("templates", {})
        result = []
        for t in templates.values():
            result.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "width_mm": t.dimensions.width_mm,
                    "height_mm": t.dimensions.height_mm,
                    "supported_printers": t.supported_printers,
                    "fields": [f.model_dump(mode="json") for f in t.fields],
                }
            )
        return json.dumps(result)

    @mcp.tool()
    async def get_template(name: str) -> str:
        """Get the full configuration of a template by name.

        Args:
            name: Template name to look up.

        Returns the full template config as JSON, or an error message if not found.
        """
        templates = _app_state.get("templates", {})
        if name not in templates:
            return json.dumps({"error": f"Template '{name}' not found"})
        template = templates[name]
        return json.dumps(template.model_dump(exclude_none=True, mode="json"))

    @mcp.tool()
    async def print_label(
        template_name: str,
        data: dict[str, Any],
        printer: str | None = None,
        quantity: int = 1,
    ) -> str:
        """Print a label using a template.

        Args:
            template_name: Name of the template to use.
            data: Dictionary of field values for the template.
            printer: Printer name to use (optional, auto-selects if not specified).
            quantity: Number of copies to print (default 1).

        Returns a JSON object with job_id, status, and message.
        """
        templates = _app_state.get("templates", {})
        printers = _app_state.get("printers", {})
        queue = _app_state.get("queue")
        jinja_engine = _app_state.get("jinja_engine")
        image_engine = _app_state.get("image_engine")

        if queue is None or jinja_engine is None:
            return json.dumps({"error": "Server not fully initialized"})

        if template_name not in templates:
            return json.dumps({"error": f"Template '{template_name}' not found"})

        template = templates[template_name]

        # Determine printer
        printer_name = printer
        if not printer_name:
            for name in printers:
                if name in template.supported_printers:
                    printer_name = name
                    break

        if not printer_name:
            return json.dumps({"error": "No printer specified and no compatible printer found"})

        if printer_name not in printers:
            return json.dumps({"error": f"Printer '{printer_name}' not found"})

        target_printer = printers[printer_name]

        if printer_name not in template.supported_printers:
            return json.dumps({"error": f"Printer '{printer_name}' not in template's supported_printers list"})

        render_context = {**data, "quantity": quantity}

        try:
            if template.engine == EngineType.IMAGE:
                if not image_engine:
                    return json.dumps({"error": "Image engine not initialized"})
                output_format = target_printer.config.type.value
                rendered = image_engine.render(template, render_context, output_format=output_format)
            else:
                rendered = jinja_engine.render(template, render_context)
        except Exception as e:
            return json.dumps({"error": f"Template rendering failed: {e}"})

        job = PrintJob(
            template_name=template_name,
            printer_name=printer_name,
            data=data,
            quantity=quantity,
            rendered_content=rendered,
        )

        await queue.submit(job)

        return json.dumps(
            {
                "job_id": str(job.id),
                "status": job.status.value,
                "message": "Label submitted for printing",
            }
        )

    @mcp.tool()
    async def create_template(template_json: str) -> str:
        """Create a new label template.

        Args:
            template_json: JSON string of the template configuration. Must include at minimum
                'name' and 'dimensions' (with width_mm/height_mm). See get_template for
                the full schema.

        Returns a JSON object with name, status ("created"), and message.
        """
        templates = _app_state.get("templates", {})
        templates_path = _app_state.get("templates_path")
        if templates_path is None:
            return json.dumps({"error": "Templates path not configured"})

        try:
            data = json.loads(template_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        try:
            template = TemplateConfig.model_validate(data)
        except Exception as e:
            return json.dumps({"error": f"Invalid template schema: {e}"})

        try:
            create_template_on_disk(template, templates_path, templates)
        except TemplateCRUDError as e:
            return json.dumps({"error": e.message})

        return json.dumps(
            {
                "name": template.name,
                "status": "created",
                "message": f"Template '{template.name}' created",
            }
        )

    @mcp.tool()
    async def update_template(name: str, template_json: str) -> str:
        """Update an existing label template.

        Args:
            name: Name of the template to update (must already exist).
            template_json: JSON string of the updated template configuration.

        Returns a JSON object with name, status ("updated"), and message.
        """
        templates = _app_state.get("templates", {})
        templates_path = _app_state.get("templates_path")
        if templates_path is None:
            return json.dumps({"error": "Templates path not configured"})

        try:
            data = json.loads(template_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        try:
            template = TemplateConfig.model_validate(data)
        except Exception as e:
            return json.dumps({"error": f"Invalid template schema: {e}"})

        try:
            update_template_on_disk(name, template, templates_path, templates)
        except TemplateCRUDError as e:
            return json.dumps({"error": e.message})

        return json.dumps(
            {
                "name": template.name,
                "status": "updated",
                "message": f"Template '{name}' updated",
            }
        )

    return mcp
