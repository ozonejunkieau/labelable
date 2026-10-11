"""FastUI web interface for Labelable."""

import base64
import logging
from datetime import datetime
from importlib.metadata import version
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastui import AnyComponent, FastUI
from fastui import components as c
from fastui.components.display import DisplayLookup
from fastui.events import GoToEvent, PageEvent
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

from labelable.models.job import MAX_QUANTITY as MAX_UI_QUANTITY
from labelable.models.template import EngineType, FieldType
from labelable.printers import dpi_mismatch_warning

router = APIRouter()

# Version and project info
try:
    __version__ = version("labelable")
except Exception:
    __version__ = "dev"
GITHUB_URL = "https://github.com/ozonejunkieau/labelable"

# Application state references (set during startup)
_app_state: dict[str, Any] = {}


# Table row models for FastUI (requires Pydantic models, not dicts)
class PrinterRow(BaseModel):
    """Row model for printers table."""

    name: str
    type: str
    connection: str
    model: str
    status: str
    queue: str
    last_checked: str


class FieldRow(BaseModel):
    """Row model for template fields table."""

    name: str
    type: str
    required: str
    default: str


# Custom HTML template with dark mode support
# {api_root_url} is the FastUI API root URL (e.g., /api or /api/hassio_ingress/<token>/api)
_CUSTOM_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="fastui:APIRootUrl" content="{api_root_url}" />
    <meta name="fastui:APIPathStrip" content="{path_strip}" />
    <title>{title}</title>
    <script type="module" crossorigin \
src="https://cdn.jsdelivr.net/npm/@pydantic/fastui-prebuilt@0.0.26/dist/assets/index.js"></script>
    <link rel="stylesheet" crossorigin \
href="https://cdn.jsdelivr.net/npm/@pydantic/fastui-prebuilt@0.0.26/dist/assets/index.css">
    <style>
      /* Mobile responsiveness */
      @media (max-width: 576px) {{
        .table {{
          font-size: 0.875rem;
        }}
        .container {{
          padding-left: 0.5rem;
          padding-right: 0.5rem;
        }}
        h2 {{
          font-size: 1.5rem;
        }}
        h4 {{
          font-size: 1.1rem;
        }}
      }}

      /* Make tables horizontally scrollable on mobile */
      .table-responsive {{
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }}

      /* Card hover effect */
      .card {{
        transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
      }}
      .card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      }}

      /* Light mode - ensure good contrast */
      .form-check-input {{
        border-color: #6c757d;
      }}
      .form-check-label {{
        color: #212529;
      }}
      .form-select, select {{
        color: #212529;
        background-color: #fff;
        border-color: #6c757d;
      }}
      .form-select option, select option {{
        color: #212529;
        background-color: #fff;
      }}
      .dropdown-menu {{
        background-color: #fff;
        border-color: #dee2e6;
      }}
      .dropdown-item {{
        color: #212529;
      }}
      .dropdown-item:hover, .dropdown-item:focus {{
        background-color: #e9ecef;
        color: #212529;
      }}
      /* FastUI react-select - light mode */
      .fastui-react-select__control {{
        background-color: #fff !important;
        border-color: #6c757d !important;
      }}
      .fastui-react-select__placeholder {{
        color: #6c757d !important;
      }}
      .fastui-react-select__single-value {{
        color: #212529 !important;
        padding-left: 4px !important;
      }}
      .fastui-react-select__input-container {{
        color: #212529 !important;
      }}
      .fastui-react-select__menu {{
        background-color: #fff !important;
        border-color: #dee2e6 !important;
      }}
      .fastui-react-select__option {{
        color: #212529 !important;
        background-color: #fff !important;
        padding-left: 12px !important;
      }}
      .fastui-react-select__option--is-focused {{
        background-color: #e9ecef !important;
      }}
      .fastui-react-select__option--is-selected {{
        background-color: #0d6efd !important;
        color: #fff !important;
      }}

      /* Spacing fixes */
      .btn + .table {{
        margin-top: 1rem;
      }}
      form {{
        margin-bottom: 2rem;
      }}

      /* Hidden form - completely invisible, triggered by external button */
      .hidden-form-fields {{
        position: absolute;
        left: -9999px;
        visibility: hidden;
      }}

      /* Footer styling - high contrast override */
      footer {{
        margin-top: 2rem;
        padding: 1rem 0;
        border-top: 1px solid #dee2e6;
        text-align: center;
        font-size: 0.875rem;
      }}
      footer a,
      footer a.text-muted,
      footer .nav-link,
      footer .nav-link.text-muted,
      footer * {{
        color: #0d6efd !important;
        text-decoration: none;
      }}
      footer a:hover,
      footer a.text-muted:hover,
      footer .nav-link:hover,
      footer .nav-link.text-muted:hover {{
        color: #0a58ca !important;
        text-decoration: underline;
      }}

      @media (prefers-color-scheme: dark) {{
        :root {{
          color-scheme: dark;
        }}
        body {{
          background-color: #212529;
          color: #f8f9fa;
        }}
        .navbar, .navbar-expand-lg {{
          background-color: #343a40 !important;
        }}
        .navbar a, .navbar .navbar-brand {{
          color: #f8f9fa !important;
        }}
        .card {{
          background-color: #2b3035;
          color: #f8f9fa;
          border-color: #495057;
        }}
        .table {{
          --bs-table-bg: #2b3035;
          --bs-table-color: #f8f9fa;
          --bs-table-border-color: #495057;
          color: #f8f9fa;
        }}
        .table td, .table th, .table tr {{
          color: #f8f9fa !important;
          background-color: #2b3035;
        }}
        .table thead {{
          color: #f8f9fa;
        }}
        .form-control, .form-select, input, select, textarea {{
          background-color: #343a40 !important;
          color: #f8f9fa !important;
          border-color: #495057 !important;
        }}
        .form-control:focus, .form-select:focus, input:focus, select:focus {{
          background-color: #3d4349 !important;
          color: #f8f9fa !important;
          border-color: #86b7fe !important;
        }}
        select option {{
          background-color: #343a40;
          color: #f8f9fa;
        }}
        /* Radio buttons and checkboxes */
        .form-check-input {{
          background-color: #343a40;
          border-color: #6c757d;
        }}
        .form-check-input:checked {{
          background-color: #0d6efd;
          border-color: #0d6efd;
        }}
        .form-check-label {{
          color: #f8f9fa !important;
        }}
        /* Dropdown menus */
        .dropdown-menu {{
          background-color: #343a40;
          border-color: #495057;
        }}
        .dropdown-item {{
          color: #f8f9fa;
        }}
        .dropdown-item:hover, .dropdown-item:focus {{
          background-color: #495057;
          color: #f8f9fa;
        }}
        .form-label, label {{
          color: #f8f9fa !important;
        }}
        .form-text, .text-muted {{
          color: #adb5bd !important;
        }}
        a {{
          color: #6ea8fe;
        }}
        a:hover {{
          color: #9ec5fe;
        }}
        .btn-primary {{
          background-color: #0d6efd;
          border-color: #0d6efd;
        }}
        .container, .container-fluid {{
          background-color: #212529;
        }}
        h1, h2, h3, h4, h5, h6, p {{
          color: #f8f9fa;
        }}
        /* FastUI react-select - dark mode */
        .fastui-react-select__control {{
          background-color: #343a40 !important;
          border-color: #495057 !important;
        }}
        .fastui-react-select__placeholder {{
          color: #adb5bd !important;
        }}
        .fastui-react-select__single-value {{
          color: #f8f9fa !important;
          padding-left: 4px !important;
        }}
        .fastui-react-select__input-container {{
          color: #f8f9fa !important;
        }}
        .fastui-react-select__menu {{
          background-color: #343a40 !important;
          border-color: #495057 !important;
        }}
        .fastui-react-select__option {{
          color: #f8f9fa !important;
          background-color: #343a40 !important;
          padding-left: 12px !important;
        }}
        .fastui-react-select__option--is-focused {{
          background-color: #495057 !important;
        }}
        .fastui-react-select__option--is-selected {{
          background-color: #0d6efd !important;
          color: #fff !important;
        }}
        .fastui-react-select__indicator {{
          color: #adb5bd !important;
        }}
        .fastui-react-select__indicator:hover {{
          color: #f8f9fa !important;
        }}
        /* Footer dark mode */
        footer {{
          background-color: #212529;
          border-top-color: #495057;
        }}
        footer a,
        footer a.text-muted,
        footer .nav-link,
        footer .nav-link.text-muted,
        footer * {{
          color: #f8f9fa !important;
        }}
        footer a:hover,
        footer a.text-muted:hover,
        footer .nav-link:hover,
        footer .nav-link.text-muted:hover {{
          color: #6ea8fe !important;
        }}
      }}
    </style>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""


def set_app_state(
    printers: dict,
    templates: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any = None,
    user_mapping: dict[str, str] | None = None,
    default_user: str = "",
    templates_path: Any = None,
    template_warnings: list[str] | None = None,
    fonts_dir: Any = None,
    download_google_fonts: bool = False,
) -> None:
    """Set application state references for the UI."""
    _app_state["printers"] = printers
    _app_state["templates"] = templates
    _app_state["queue"] = queue
    _app_state["jinja_engine"] = jinja_engine
    _app_state["image_engine"] = image_engine
    _app_state["user_mapping"] = user_mapping or {}
    _app_state["default_user"] = default_user
    _app_state["templates_path"] = templates_path
    _app_state["template_warnings"] = template_warnings or []
    # Needed so the reload-templates button validates fonts and downloads
    # missing ones exactly as startup does.
    _app_state["fonts_dir"] = fonts_dir
    _app_state["download_google_fonts"] = download_google_fonts


def _connection_summary(conn: Any) -> str:
    """Short human description of how a printer is reached, for every transport."""
    kind = getattr(conn, "type", "")
    if kind == "tcp":
        return f"TCP {conn.host}:{conn.port}"
    if kind == "serial":
        return f"Serial {conn.device}"
    if kind == "usb":
        return "USB"
    if kind == "ha":
        return f"Home Assistant ({conn.device_id})"
    if kind == "bridge":
        return f"Bridge daemon ({conn.serial_number})"
    if kind == "ptouch_bridge":
        return f"P-Touch bridge {conn.host}"
    if kind == "zebra_bridge":
        return f"Zebra bridge {conn.host}"
    return str(kind)


def _status_text(printer: Any) -> str:
    """Online/Offline/Checking, with the printer's own reason when it has one."""
    cached = printer.get_cached_online_status()
    if cached is None:
        return "Checking..."
    if cached:
        return "Online"
    errors = getattr(printer, "errors", None)
    if isinstance(errors, list) and errors:
        return "Offline: " + ", ".join(str(e).replace("_", " ") for e in errors)
    return "Offline"


def _printer_status_line(name: str, printer: Any, queue: Any) -> str:
    """One line of status for a printer on the print form."""
    status = _status_text(printer)
    line = f"Printer: {name} - {status}"
    if status.startswith("Offline"):
        line += " (jobs will wait in the queue)"
    line += f" - {_connection_summary(printer.config.connection)}"
    queue_size = queue.get_queue_size(name) if queue else 0
    if queue_size > 0:
        line += f" [{queue_size} queued]"
    return line


def _size_text(template: Any) -> str:
    """Label size, as a diameter for circular labels (which have no width/height)."""
    dims = template.dimensions
    if dims.diameter_mm:
        return f"{dims.diameter_mm:g} mm diameter"
    return f"{dims.width_mm:g} x {dims.height_mm:g} mm"


def _page_wrapper(*components: AnyComponent, title: str = "Labelable") -> list[AnyComponent]:
    """Wrap components in a standard page layout."""
    return [
        c.PageTitle(text=title),
        c.Navbar(
            title="Labelable",
            title_event=GoToEvent(url="/"),
            start_links=[
                c.Link(
                    components=[c.Text(text="Templates")],
                    on_click=GoToEvent(url="/"),
                ),
                c.Link(
                    components=[c.Text(text="Printers")],
                    on_click=GoToEvent(url="/printers"),
                ),
            ],
        ),
        c.Page(components=list(components)),
        c.Footer(
            links=[
                c.Link(
                    components=[c.Text(text=f"Labelable v{__version__}")],
                    on_click=GoToEvent(url=GITHUB_URL),
                ),
            ],
        ),
    ]


@router.get("/api/", response_model=FastUI, response_model_exclude_none=True)
async def home(request: Request) -> list[AnyComponent]:
    """Home page - list of templates."""
    from labelable.config import settings

    templates = _app_state.get("templates", {})

    if not templates:
        return _page_wrapper(
            c.Heading(text="Label Templates", level=2),
            c.Paragraph(text="No templates configured. Add YAML files to templates directory."),
        )

    # Build cards for each template using FastUI Div components
    template_cards = []
    for template in templates.values():
        template_cards.append(
            c.Div(
                class_name="col-md-6 col-lg-4 mb-3",
                components=[
                    c.Link(
                        on_click=GoToEvent(url=f"/print/{template.name}"),
                        components=[
                            c.Div(
                                class_name="card h-100",
                                components=[
                                    c.Div(
                                        class_name="card-body",
                                        components=[
                                            c.Heading(text=template.name, level=5),
                                            c.Paragraph(text=template.description or "No description"),
                                            c.Text(text=f"Size: {_size_text(template)}"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )

    # Build page components
    page_components: list[AnyComponent] = [
        c.Heading(text="Label Templates", level=2),
    ]

    # Display any template warnings
    template_warnings = _app_state.get("template_warnings", [])
    if template_warnings:
        warning_items = [c.Paragraph(text=w) for w in template_warnings]
        page_components.append(
            c.Div(
                class_name="alert alert-warning",
                components=[
                    c.Heading(text="⚠️ Template Warnings", level=5),
                    *warning_items,
                ],
            )
        )

    page_components.extend(
        [
            c.Paragraph(text="Select a template to print:"),
            c.Div(class_name="row", components=template_cards),
            c.Link(
                components=[c.Text(text="Reload Templates")],
                on_click=GoToEvent(url="/reload-templates"),
                class_name="btn btn-secondary mt-3",
            ),
        ]
    )

    # Add user debug info if enabled
    if settings.show_user_debug:
        # HA Ingress provides X-Remote-User-Id, X-Remote-User-Name, X-Remote-User-Display-Name
        ha_user_id = request.headers.get("X-Remote-User-Id", "")
        ha_user_name = request.headers.get("X-Remote-User-Name", "")
        ha_display_name = request.headers.get("X-Remote-User-Display-Name", "")
        user_mapping = _app_state.get("user_mapping", {})
        resolved_user = user_mapping.get(ha_user_id, _app_state.get("default_user", ""))

        if ha_user_id:
            debug_text = f"HA User: {ha_display_name or ha_user_name or ha_user_id}"
            if resolved_user:
                debug_text += f" → {resolved_user}"
            elif ha_display_name:
                debug_text += " (not in user_mapping, using display name)"
            else:
                debug_text += " (not in user_mapping)"
        else:
            debug_text = "No X-Remote-User-Id header detected (not via HA Ingress?)"

        page_components.append(
            c.Div(
                class_name="mt-4 text-muted small",
                components=[c.Text(text=debug_text)],
            )
        )

    return _page_wrapper(*page_components)


@router.get("/api/printers", response_model=FastUI, response_model_exclude_none=True)
async def printers_page() -> list[AnyComponent]:
    """Printers status page."""
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")

    if not printers:
        return _page_wrapper(
            c.Heading(text="Printers", level=2),
            c.Paragraph(text="No printers configured. Add definitions to config.yaml."),
            title="Printers - Labelable",
        )

    rows = []
    for name, printer in printers.items():
        # Use cached status to avoid blocking page render
        status = _status_text(printer)

        # Format last checked time
        if printer.last_checked:
            last_checked = printer.last_checked.strftime("%H:%M:%S")
        else:
            last_checked = "-"

        queue_size = queue.get_queue_size(name) if queue else 0
        rows.append(
            PrinterRow(
                name=name,
                type=str(printer.config.type),
                connection=_connection_summary(printer.config.connection),
                model=printer.model_info or "-",
                status=status,
                queue=str(queue_size),
                last_checked=last_checked,
            )
        )

    return _page_wrapper(
        c.Heading(text="Printers", level=2),
        # Nothing listened for the old PageEvent("reload"), so the button did
        # nothing; navigating with a fresh query re-fetches the page
        c.Button(
            text="Refresh",
            on_click=GoToEvent(url="/printers", query={"t": str(int(datetime.now().timestamp()))}),
        ),
        c.Table(
            data=rows,
            columns=[
                DisplayLookup(field="name", title="Name"),
                DisplayLookup(field="type", title="Type"),
                DisplayLookup(field="connection", title="Connection"),
                DisplayLookup(field="model", title="Model"),
                DisplayLookup(field="status", title="Status"),
                DisplayLookup(field="queue", title="Queue"),
                DisplayLookup(field="last_checked", title="Last Checked"),
            ],
        ),
        title="Printers - Labelable",
    )


@router.get("/api/reload-templates", response_model=FastUI, response_model_exclude_none=True)
@router.post("/api/reload-templates", response_model=FastUI, response_model_exclude_none=True)
async def reload_templates() -> list[AnyComponent]:
    """Reload templates from disk and refresh printer status."""
    import logging

    from labelable.config import load_templates

    logger = logging.getLogger(__name__)

    templates_path = _app_state.get("templates_path")
    details: list[str] = []
    if templates_path:
        logger.info(f"Reloading templates from {templates_path}")
        # Must mirror the startup call: without fonts_dir, load_templates
        # skips font validation entirely and a template with a missing font
        # loads silently and renders with the PIL fallback face.
        result = load_templates(
            templates_path,
            fonts_dir=_app_state.get("fonts_dir"),
            download_google_fonts=_app_state.get("download_google_fonts", False),
        )
        _app_state["templates"].clear()
        _app_state["templates"].update(result.templates)
        _app_state["template_warnings"] = result.warnings
        count = len(result.templates)
        message = f"Reloaded {count} template{'s' if count != 1 else ''} from {templates_path}"
        # Build details for each template
        for name, tmpl in result.templates.items():
            printers = ", ".join(tmpl.supported_printers) if tmpl.supported_printers else "(none)"
            details.append(f"{name}: supported_printers=[{printers}]")
            logger.info(f"  Loaded template '{name}' with supported_printers={tmpl.supported_printers}")
    else:
        message = "Templates path not configured."

    # Refresh printer status cache
    printers = _app_state.get("printers", {})
    for printer in printers.values():
        printer.invalidate_cache()
    logger.info(f"Invalidated status cache for {len(printers)} printer(s)")

    components: list[AnyComponent] = [
        c.Heading(text="Templates Reloaded", level=2),
        c.Paragraph(text=message),
    ]
    if details:
        components.append(c.Heading(text="Loaded Templates", level=4))
        for detail in details:
            components.append(c.Paragraph(text=detail))
    components.append(
        c.Link(
            components=[c.Text(text="← Back to templates")],
            on_click=GoToEvent(url="/"),
        ),
    )

    return _page_wrapper(*components, title="Reload - Labelable")


@router.get(
    "/api/print/{template_name}",
    response_model=FastUI,
    response_model_exclude_none=True,
)
async def print_form(request: Request, template_name: str) -> list[AnyComponent]:
    """Print form for a specific template."""
    # Get API root URL for form submission (FastUI doesn't transform submit URLs)
    root_path = request.scope.get("root_path", "")
    api_root = f"{root_path}/api" if root_path else "/api"

    templates = _app_state.get("templates", {})
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")

    if template_name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    template = templates[template_name]

    # Build list of compatible printers first (needed to decide what to show)
    compatible_printers = [
        (name, f"{name} ({printer.config.type})")
        for name, printer in printers.items()
        if name in template.supported_printers
    ]

    # Build template info components
    supported_names = ", ".join(template.supported_printers) if template.supported_printers else "None specified"
    template_info: list[AnyComponent] = [
        c.Heading(text=template.name, level=2),
        c.Paragraph(text=template.description or "No description"),
        c.Paragraph(text=f"Label size: {_size_text(template)}"),
    ]

    # Only show detailed field table when no printers available (review mode)
    # When printers are available, the form itself shows the fields
    if not compatible_printers:
        template_info.append(c.Paragraph(text=f"Supported printers: {supported_names}"))
        if template.fields:
            field_rows = []
            for field in template.fields:
                default_str = str(field.default) if field.default is not None else "-"
                field_rows.append(
                    FieldRow(
                        name=field.name,
                        type=str(field.type),
                        required="Yes" if field.required else "No",
                        default=default_str,
                    )
                )
            template_info.append(c.Heading(text="Fields", level=4))
            template_info.append(
                c.Table(
                    data=field_rows,
                    columns=[
                        DisplayLookup(field="name", title="Name"),
                        DisplayLookup(field="type", title="Type"),
                        DisplayLookup(field="required", title="Required"),
                        DisplayLookup(field="default", title="Default"),
                    ],
                )
            )

    # Determine form or status message
    if not printers:
        printer_status = c.Paragraph(
            text="No printers configured. Add printer definitions to config.yaml to enable printing."
        )
        form_components: list[AnyComponent] = [
            c.Heading(text="Print", level=4),
            printer_status,
        ]
    elif not compatible_printers:
        printer_status = c.Paragraph(
            text=f"No compatible printers available. This template supports: {supported_names}. "
            f"Check your config.yaml and template's supported_printers list."
        )
        form_components = [
            c.Heading(text="Print", level=4),
            printer_status,
        ]
    else:
        # Build printer status badges when we have compatible printers
        if len(compatible_printers) == 1:
            # Single printer - show status (use cached to avoid blocking)
            printer_name = compatible_printers[0][0]
            form_components = [
                c.Heading(text="Print", level=4),
                c.Paragraph(text=_printer_status_line(printer_name, printers[printer_name], queue)),
            ]
            # Show fixed quantity if set
            if template.quantity is not None:
                form_components.append(c.Paragraph(text=f"Quantity: {template.quantity} (fixed)"))
            # For image templates, submit to preview endpoint first
            if template.engine == EngineType.IMAGE:
                form_components.append(
                    c.Paragraph(
                        text="Preview will be shown before printing.",
                        class_name="text-muted small",
                    ),
                )
                form_components.append(
                    c.ModelForm(
                        model=_create_form_model(template, None),
                        submit_url=f"{api_root}/print/{template_name}/preview?printer={quote(printer_name, safe='')}",
                        display_mode="default",
                    ),
                )
            else:
                form_components.append(
                    c.ModelForm(
                        model=_create_form_model(template, None),
                        submit_url=f"{api_root}/print/{template_name}/submit?printer={quote(printer_name, safe='')}",
                        display_mode="default",
                    ),
                )
        else:
            # Several printers: show each one's status, not just the dropdown
            form_components = [
                c.Heading(text="Print", level=4),
                *[
                    c.Paragraph(text=_printer_status_line(name, printers[name], queue), class_name="mb-1")
                    for name, _ in compatible_printers
                ],
            ]
            # Show fixed quantity if set
            if template.quantity is not None:
                form_components.append(c.Paragraph(text=f"Quantity: {template.quantity} (fixed)"))
            # For image templates, submit to preview endpoint first
            if template.engine == EngineType.IMAGE:
                form_components.append(
                    c.Paragraph(
                        text="Preview will be shown before printing.",
                        class_name="text-muted small",
                    ),
                )
                form_components.append(
                    c.ModelForm(
                        model=_create_form_model(template, compatible_printers),
                        submit_url=f"{api_root}/print/{template_name}/preview",
                        display_mode="default",
                    ),
                )
            else:
                form_components.append(
                    c.ModelForm(
                        model=_create_form_model(template, compatible_printers),
                        submit_url=f"{api_root}/print/{template_name}/submit",
                        display_mode="default",
                    ),
                )

    return _page_wrapper(
        *template_info,
        *form_components,
        c.Link(
            components=[c.Text(text="<- Back to templates")],
            on_click=GoToEvent(url="/"),
        ),
        title=f"{template.name} - Labelable",
    )


def _resolve_user(request: Request) -> str:
    """Resolve the current user from request headers or config.

    When accessed via Home Assistant Ingress, headers are provided:
    - X-Remote-User-Id: The HA user's UUID
    - X-Remote-User-Name: The username
    - X-Remote-User-Display-Name: The display name

    The user_id is mapped to a display name via user_mapping in config.yaml.
    If not in mapping, falls back to display name, then username, then default.
    """
    user_mapping = _app_state.get("user_mapping", {})
    default_user = _app_state.get("default_user", "")

    # Check for Home Assistant user headers
    ha_user_id = request.headers.get("X-Remote-User-Id")
    ha_display_name = request.headers.get("X-Remote-User-Display-Name")
    ha_user_name = request.headers.get("X-Remote-User-Name")

    # Try user_mapping first
    if ha_user_id and ha_user_id in user_mapping:
        return user_mapping[ha_user_id]

    # Fall back to display name or username from HA
    if ha_display_name:
        return ha_display_name
    if ha_user_name:
        return ha_user_name

    return default_user


@router.post(
    "/api/print/{template_name}/submit",
    response_model=FastUI,
    response_model_exclude_none=True,
)
async def submit_print(
    request: Request,
    template_name: str,
    printer: str | None = None,
) -> list[AnyComponent]:
    """Handle print form submission."""
    templates = _app_state.get("templates", {})
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")
    jinja_engine = _app_state.get("jinja_engine")

    if queue is None:
        raise HTTPException(status_code=500, detail="Print queue not initialized")
    if jinja_engine is None:
        raise HTTPException(status_code=500, detail="Template engine not initialized")

    if template_name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    template = templates[template_name]

    # Parse form data directly from request
    form_raw = await request.form()
    form_printer = form_raw.get("printer")
    printer_name = printer or (form_printer if isinstance(form_printer, str) else None)
    form_data, quantity, form_error = _parse_print_form(form_raw, template)
    if form_error:
        return _form_error_page(template_name, form_error)

    # Populate USER fields from request context
    current_user = _resolve_user(request)
    for field in template.fields:
        if field.type == FieldType.USER:
            form_data[field.name] = current_user

    # Add quantity to context for template use (e.g., "1 of {{ quantity }}")
    form_data["quantity"] = quantity

    if not printer_name or printer_name not in printers or printer_name not in template.supported_printers:
        return _page_wrapper(
            c.Heading(text="Error", level=2),
            c.Paragraph(text="Invalid printer selected."),
            c.Link(
                components=[c.Text(text="<- Try again")],
                on_click=GoToEvent(url=f"/print/{template_name}"),
            ),
        )

    printer_obj = printers[printer_name]

    # Render template using the appropriate engine
    try:
        if template.engine == EngineType.IMAGE:
            image_engine = _app_state.get("image_engine")
            if not image_engine:
                raise RuntimeError("Image engine not initialized")
            # Determine output format from printer type
            output_format = printer_obj.output_format  # "zpl", "epl2", "ptouch", "ptouch_raw"
            rendered = image_engine.render(template, form_data, output_format=output_format)
            if dpi_warning := dpi_mismatch_warning(printer_obj, template):
                logging.getLogger(__name__).warning(dpi_warning)
        else:
            rendered = jinja_engine.render(template, form_data)
    except Exception as e:
        return _page_wrapper(
            c.Heading(text="Error", level=2),
            c.Paragraph(text=f"Template rendering failed: {e}"),
            c.Link(
                components=[c.Text(text="<- Try again")],
                on_click=GoToEvent(url=f"/print/{template_name}"),
            ),
        )

    # Create and submit job
    from labelable.models.job import PrintJob

    job = PrintJob(
        template_name=template_name,
        printer_name=printer_name,
        data=form_data,
        quantity=quantity,
        rendered_content=rendered,
    )

    await queue.submit(job)

    # Navigate to the job page rather than rendering a confirmation here: the
    # preview flow submits from a hidden form, and anything rendered in its
    # place is invisible, so users saw no feedback and could print twice.
    return [c.FireEvent(event=GoToEvent(url=f"/jobs/{job.id}"))]


class JobFieldRow(BaseModel):
    """Row model for the job page's field values table."""

    field: str
    value: str


# How each job status reads on the job page
JOB_STATUS_TEXT = {
    "pending": "Waiting in the print queue",
    "printing": "Printing",
    "completed": "Printed",
    "failed": "Failed",
    "expired": "Expired - the printer was unavailable for too long",
}


@router.get("/api/jobs/{job_id}", response_model=FastUI, response_model_exclude_none=True)
async def job_page(job_id: str) -> list[AnyComponent]:
    """Show a print job's status and the values it was printed with."""
    queue = _app_state.get("queue")
    job = queue.get_job(job_id) if queue else None
    if job is None:
        return _page_wrapper(
            c.Heading(text="Job not found", level=2),
            c.Paragraph(text="Jobs are kept in memory and are lost when Labelable restarts."),
            c.Link(components=[c.Text(text="<- Back to templates")], on_click=GoToEvent(url="/")),
            title="Job not found - Labelable",
        )

    status_text = JOB_STATUS_TEXT.get(str(job.status), str(job.status))
    details = [f"Printer: {job.printer_name}", f"Quantity: {job.quantity}"]
    if str(job.status) == "pending":
        printer = _app_state.get("printers", {}).get(job.printer_name)
        if printer is not None and printer.get_cached_online_status() is False:
            details.append("The printer is offline; the job will print when it comes back.")

    rows = [
        JobFieldRow(
            field=name.replace("_", " ").title(),
            # Show list fields one item per line's worth, compactly
            value=", ".join(line.strip() for line in str(value).splitlines() if line.strip())
            if isinstance(value, str) and "\n" in value
            else str(value),
        )
        for name, value in job.data.items()
        if name != "quantity"
    ]

    components: list[AnyComponent] = [
        c.Heading(text=job.template_name, level=2),
        c.Heading(text=status_text, level=4),
        *[c.Paragraph(text=line) for line in details],
    ]
    if job.error_message:
        components.append(c.Paragraph(text=f"Error: {job.error_message}", class_name="text-danger"))
    if rows:
        components.append(
            c.Table(
                data=rows,
                columns=[DisplayLookup(field="field", title="Field"), DisplayLookup(field="value", title="Value")],
            )
        )
    components.append(
        c.Div(
            class_name="d-flex gap-3 mt-3",
            components=[
                c.Link(
                    components=[c.Text(text="Refresh")],
                    on_click=GoToEvent(url=f"/jobs/{job.id}", query={"t": str(int(datetime.now().timestamp()))}),
                    class_name="btn btn-secondary",
                ),
                c.Link(
                    components=[c.Text(text="Print another")],
                    on_click=GoToEvent(url=f"/print/{job.template_name}"),
                    class_name="btn btn-primary",
                ),
                c.Link(components=[c.Text(text="<- Back to templates")], on_click=GoToEvent(url="/")),
            ],
        )
    )
    components.append(c.Paragraph(text=f"Job ID: {job.id}", class_name="text-muted small mt-3"))
    return _page_wrapper(*components, title=f"Print job - {job.template_name}")


@router.post(
    "/api/print/{template_name}/preview",
    response_model=FastUI,
    response_model_exclude_none=True,
)
async def preview_label(
    request: Request,
    template_name: str,
    printer: str | None = None,
) -> list[AnyComponent]:
    """Show label preview with option to print."""
    # Get API root URL for form submission
    root_path = request.scope.get("root_path", "")
    api_root = f"{root_path}/api" if root_path else "/api"

    templates = _app_state.get("templates", {})
    printers = _app_state.get("printers", {})

    template = templates.get(template_name)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    if template.engine != EngineType.IMAGE:
        raise HTTPException(status_code=400, detail="Preview is only available for image templates")

    # Parse form data
    form_raw = await request.form()
    form_printer = form_raw.get("printer")
    printer_name = printer or (form_printer if isinstance(form_printer, str) else None)
    form_data, quantity, form_error = _parse_print_form(form_raw, template)
    if form_error:
        return _form_error_page(template_name, form_error)

    if not printer_name or printer_name not in printers or printer_name not in template.supported_printers:
        return _page_wrapper(
            c.Heading(text="Error", level=2),
            c.Paragraph(text="Invalid printer selected."),
            c.Link(
                components=[c.Text(text="<- Try again")],
                on_click=GoToEvent(url=f"/print/{template_name}"),
            ),
        )

    # Handle auto-populated fields
    for field in template.fields:
        if field.type == FieldType.DATETIME:
            form_data[field.name] = datetime.now().strftime(field.format or "%Y-%m-%d %H:%M")
        elif field.type == FieldType.USER:
            form_data[field.name] = _resolve_user(request)

    # Add quantity to context for template use
    form_data["quantity"] = quantity

    # Render preview image
    image_engine = _app_state.get("image_engine")
    if not image_engine:
        raise HTTPException(status_code=500, detail="Image engine not initialized")

    try:
        png_bytes = image_engine.render_preview(template, form_data)
    except Exception as e:
        return _page_wrapper(
            c.Heading(text="Preview Error", level=2),
            c.Paragraph(text=f"Failed to render preview: {e}"),
            c.Link(
                components=[c.Text(text="<- Try again")],
                on_click=GoToEvent(url=f"/print/{template_name}"),
            ),
            title=f"Preview Error - {template.name}",
        )

    # Convert to base64 data URL
    b64_image = base64.b64encode(png_bytes).decode("ascii")

    # Build hidden form fields for printing
    # We need to pass all form data to the print endpoint
    hidden_fields: dict[str, Any] = {}
    for key, value in form_data.items():
        if key != "quantity":  # quantity handled separately
            hidden_fields[key] = value

    # Create a model for the hidden print form
    HiddenPrintModel = _create_hidden_form_model(hidden_fields, quantity)

    # Build clean, focused preview page
    # The form fields are hidden via CSS (hidden-form-fields class)
    quantity_text = f"Quantity: {quantity}" if quantity > 1 else ""

    return _page_wrapper(
        c.Heading(text="Label Preview", level=2),
        # Centered image container
        c.Div(
            class_name="text-center my-4",
            components=[
                c.Image(
                    src=f"data:image/png;base64,{b64_image}",
                    alt=f"Preview of {template.name}",
                    class_name="img-fluid border rounded",
                ),
            ],
        ),
        # Quantity (only shown if > 1)
        *([c.Paragraph(text=quantity_text, class_name="text-center text-muted")] if quantity > 1 else []),
        # Button row: Back and Print side by side
        c.Div(
            class_name="d-flex justify-content-center gap-3 mt-4",
            components=[
                c.Link(
                    components=[c.Text(text="Back")],
                    on_click=GoToEvent(url=f"/print/{template_name}"),
                    class_name="btn btn-secondary",
                ),
                c.Button(
                    text="Print",
                    on_click=PageEvent(name="print-form"),
                    class_name="btn btn-primary",
                ),
            ],
        ),
        # Hidden form - triggered by Print button above
        c.Div(
            class_name="hidden-form-fields",
            components=[
                c.ModelForm(
                    model=HiddenPrintModel,
                    submit_url=f"{api_root}/print/{template_name}/submit?printer={quote(printer_name, safe='')}",
                    submit_trigger=PageEvent(name="print-form"),
                ),
            ],
        ),
        title=f"Preview - {template.name}",
    )


def _form_error_page(template_name: str, message: str) -> list[AnyComponent]:
    """Error page for a print form that could not be parsed."""
    return _page_wrapper(
        c.Heading(text="Error", level=2),
        c.Paragraph(text=message),
        c.Link(
            components=[c.Text(text="<- Try again")],
            on_click=GoToEvent(url=f"/print/{template_name}"),
        ),
    )


def _parse_print_form(form_raw: Any, template: Any) -> tuple[dict[str, Any], int, str | None]:
    """Turn a submitted print form into template data and a quantity.

    The browser posts the form's FormData as-is, which differs from what the
    template expects in three ways:

    - An unchecked checkbox is simply absent. Every boolean field is rendered
      as a checkbox, so absent means False - not "use the default", which made
      a ``default: true`` field impossible to switch off.
    - An empty number input arrives as ``""``. It is dropped so the field's
      default (or the required-field error) applies, instead of ``int("")``.
    - Quantity may be blank or junk if the user cleared the box.

    Returns:
        (form data, quantity, error message or None)
    """
    form_data: dict[str, Any] = dict(form_raw.items())
    form_data.pop("printer", None)

    for field in template.fields:
        if field.type == FieldType.BOOLEAN:
            form_data[field.name] = field.name in form_data and str(form_data[field.name]).lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
        elif field.type in (FieldType.INTEGER, FieldType.FLOAT) and form_data.get(field.name) == "":
            del form_data[field.name]

    raw_quantity = form_data.pop("quantity", None)
    if template.quantity is not None:
        return form_data, template.quantity, None
    if raw_quantity in (None, ""):
        return form_data, 1, None
    try:
        as_float = float(raw_quantity)
    except (TypeError, ValueError):
        as_float = None
    if as_float is None or not as_float.is_integer():
        return form_data, 1, f"Quantity must be a whole number, not '{raw_quantity}'."
    quantity = int(as_float)
    if not 1 <= quantity <= MAX_UI_QUANTITY:
        return form_data, quantity, f"Quantity must be between 1 and {MAX_UI_QUANTITY}."
    return form_data, quantity, None


def _create_hidden_form_model(hidden_fields: dict[str, Any], quantity: int) -> type[BaseModel]:
    """Create a Pydantic model with pre-filled field values for the print form.

    The fields are shown with their default values pre-filled so the user
    can simply click Submit to print with the previewed data.

    Args:
        hidden_fields: Dictionary of field name -> value to include.
        quantity: The quantity value.

    Returns:
        A Pydantic model class with the values as defaults.
    """
    fields: dict[str, Any] = {}

    # Add quantity field with default
    fields["quantity"] = (int, Field(default=quantity, title="Quantity"))

    # Add all fields with their values as defaults
    # Note: We can't use json_schema_extra={"type": "hidden"} as it breaks
    # FastUI's JSON schema parsing which expects valid JSON schema types
    for name, value in hidden_fields.items():
        title = name.replace("_", " ").title()
        if isinstance(value, bool):
            fields[name] = (bool, Field(default=value, title=title))
        elif isinstance(value, int):
            fields[name] = (int, Field(default=value, title=title))
        elif isinstance(value, float):
            fields[name] = (float, Field(default=value, title=title))
        else:
            text = str(value) if value else ""
            # A plain <input> strips CR/LF from its value, which collapsed a
            # batch list ("GND\nVCC\nSDA") into one label between preview and
            # print. A <textarea> keeps them. FastUI fills a textarea from
            # "initial", not from the default.
            extra: dict[str, Any] | None = (
                {"format": "textarea", "initial": text} if "\n" in text or "\r" in text else None
            )
            fields[name] = (str, Field(default=text, title=title, json_schema_extra=extra))

    # Create dynamic model
    annotations = {k: v[0] for k, v in fields.items()}
    defaults = {k: v[1] for k, v in fields.items()}
    model = type("HiddenPrintForm", (BaseModel,), {"__annotations__": annotations, **defaults})
    return model


def _create_form_model(template, compatible_printers: list[tuple[str, str]] | None) -> type[BaseModel]:
    """Create a dynamic Pydantic model for the template form.

    Args:
        template: The template configuration
        compatible_printers: List of (name, display) tuples for printer dropdown,
            or None if printer is passed via query param (single printer case)
    """
    from enum import Enum

    fields: dict[str, Any] = {}

    # Only show printer dropdown if multiple printers available
    if compatible_printers is not None and len(compatible_printers) > 1:
        PrinterEnum = Enum("PrinterEnum", {name: name for name, _ in compatible_printers})
        fields["printer"] = (PrinterEnum, Field(title="Printer"))
    # If compatible_printers is None, printer comes from query param - no field needed

    # Quantity field - only show if template doesn't have fixed quantity
    if template.quantity is None:
        fields["quantity"] = (int, Field(default=1, ge=1, title="Quantity"))

    # Template fields
    for field in template.fields:
        # Skip auto-populated fields
        if field.type == FieldType.DATETIME:
            continue  # Auto-populates with current time
        if field.type == FieldType.USER:
            continue  # Auto-populates from request context

        field_type: Any
        if field.type == FieldType.INTEGER:
            field_type = int
        elif field.type == FieldType.FLOAT:
            field_type = float
        elif field.type == FieldType.BOOLEAN:
            field_type = bool
        elif field.type == FieldType.LIST:
            field_type = str
        elif field.type == FieldType.SELECT and field.options:
            # Create enum for select fields (renders as radio buttons)
            # Filter out empty strings and use "None" as the display name for empty option
            enum_members = {}
            for opt in field.options:
                if opt == "":
                    enum_members["None"] = ""
                else:
                    enum_members[opt] = opt
            field_type = Enum(field.name.title(), enum_members)
        else:
            field_type = str

        # Add asterisk for required fields, include description
        base_title = field.name.replace("_", " ").title()
        extra_schema: dict[str, Any] | None = None
        if field.type == FieldType.LIST:
            # FastUI fills a textarea from "initial", not from the default
            extra_schema = {"format": "textarea"}
            if field.default is not None:
                extra_schema["initial"] = str(field.default)
        if field.required and field.default is None:
            title = f"{base_title} *"
            fields[field.name] = (
                field_type,
                Field(title=title, description=field.description or None, json_schema_extra=extra_schema),
            )
        else:
            fields[field.name] = (
                field_type | None,
                Field(
                    default=field.default,
                    title=base_title,
                    description=field.description or None,
                    json_schema_extra=extra_schema,
                ),
            )

    # Create dynamic model
    annotations = {k: v[0] for k, v in fields.items()}
    defaults = {k: v[1] for k, v in fields.items()}
    model = type("PrintForm", (BaseModel,), {"__annotations__": annotations, **defaults})
    return model


@router.get("/{path:path}", response_class=HTMLResponse)
async def spa_handler(request: Request, path: str) -> HTMLResponse:
    """Serve the FastUI SPA for all non-API routes."""
    # Get root_path from ASGI scope (set by IngressPathMiddleware)
    # FastUI expects APIRootUrl to be the base for /api/ endpoints
    # APIPathStrip removes the ingress prefix from browser path before appending to APIRootUrl
    root_path = request.scope.get("root_path", "")
    api_root_url = f"{root_path}/api" if root_path else "/api"
    path_strip = root_path if root_path else ""
    return HTMLResponse(_CUSTOM_HTML.format(title="Labelable", api_root_url=api_root_url, path_strip=path_strip))
