"""FastUI web interface for Labelable."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastui import AnyComponent, FastUI
from fastui import components as c
from fastui.components.display import DisplayLookup
from fastui.events import GoToEvent, PageEvent
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

router = APIRouter()

# Application state references (set during startup)
_app_state: dict[str, Any] = {}


# Table row models for FastUI (requires Pydantic models, not dicts)
class PrinterRow(BaseModel):
    """Row model for printers table."""

    name: str
    type: str
    model: str
    status: str
    queue: str


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
    user_mapping: dict[str, str] | None = None,
    default_user: str = "",
) -> None:
    """Set application state references for the UI."""
    _app_state["printers"] = printers
    _app_state["templates"] = templates
    _app_state["queue"] = queue
    _app_state["jinja_engine"] = jinja_engine
    _app_state["user_mapping"] = user_mapping or {}
    _app_state["default_user"] = default_user


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
    ]


@router.get("/api/", response_model=FastUI, response_model_exclude_none=True)
async def home() -> list[AnyComponent]:
    """Home page - list of templates."""
    templates = _app_state.get("templates", {})

    if not templates:
        return _page_wrapper(
            c.Heading(text="Label Templates", level=2),
            c.Paragraph(text="No templates configured. Add YAML files to templates directory."),
        )

    # Build cards for each template using FastUI Div components
    template_cards = []
    for template in templates.values():
        dims = template.dimensions
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
                                            c.Text(text=f"Size: {dims.width_mm} x {dims.height_mm} mm"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )

    return _page_wrapper(
        c.Heading(text="Label Templates", level=2),
        c.Paragraph(text="Select a template to print:"),
        c.Div(class_name="row", components=template_cards),
    )


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
        cached_status = printer.get_cached_online_status()
        if cached_status is None:
            status = "Unknown"
        elif cached_status:
            status = "Online"
        else:
            status = "Offline"

        # Get connection info (IP:PORT for TCP)
        conn = printer.config.connection
        if hasattr(conn, "host") and hasattr(conn, "port"):
            status = f"{status} ({conn.host}:{conn.port})"
        elif hasattr(conn, "device"):
            status = f"{status} ({conn.device})"

        queue_size = queue.get_queue_size(name) if queue else 0
        rows.append(
            PrinterRow(
                name=name,
                type=str(printer.config.type),
                model=printer.model_info or "-",
                status=status,
                queue=str(queue_size),
            )
        )

    return _page_wrapper(
        c.Heading(text="Printers", level=2),
        c.Button(text="Refresh", on_click=PageEvent(name="reload")),
        c.Table(
            data=rows,
            columns=[
                DisplayLookup(field="name", title="Name"),
                DisplayLookup(field="type", title="Type"),
                DisplayLookup(field="model", title="Model"),
                DisplayLookup(field="status", title="Status"),
                DisplayLookup(field="queue", title="Queue"),
            ],
        ),
        title="Printers - Labelable",
    )


@router.get(
    "/api/print/{template_name}",
    response_model=FastUI,
    response_model_exclude_none=True,
)
async def print_form(template_name: str) -> list[AnyComponent]:
    """Print form for a specific template."""
    templates = _app_state.get("templates", {})
    printers = _app_state.get("printers", {})
    queue = _app_state.get("queue")

    if template_name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    template = templates[template_name]
    dims = template.dimensions

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
        c.Paragraph(text=f"Label size: {dims.width_mm} x {dims.height_mm} mm"),
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
            printer = printers[printer_name]
            cached_status = printer.get_cached_online_status()
            queue_size = queue.get_queue_size(printer_name) if queue else 0

            # Get connection info
            conn = printer.config.connection
            if hasattr(conn, "host") and hasattr(conn, "port"):
                conn_info = f"{conn.host}:{conn.port}"
            elif hasattr(conn, "device"):
                conn_info = conn.device
            else:
                conn_info = ""

            # Build status text with indicators
            if cached_status is None:
                status_text = f"Printer: {printer_name}"
            elif cached_status:
                status_text = f"Printer: {printer_name} - Online"
            else:
                status_text = f"Printer: {printer_name} - Offline (will queue)"
            if conn_info:
                status_text += f" ({conn_info})"
            if queue_size > 0:
                status_text += f" [{queue_size} queued]"

            form_components = [
                c.Heading(text="Print", level=4),
                c.Paragraph(text=status_text),
                c.ModelForm(
                    model=_create_form_model(template, None),
                    submit_url=f"/print/{template_name}/submit?printer={printer_name}",
                    display_mode="default",
                ),
            ]
        else:
            form_components = [
                c.Heading(text="Print", level=4),
                c.ModelForm(
                    model=_create_form_model(template, compatible_printers),
                    submit_url=f"/print/{template_name}/submit",
                    display_mode="default",
                ),
            ]

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

    When accessed via Home Assistant Ingress, the X-Hass-User-Id header
    contains the HA user's UUID. This is mapped to a display name via
    user_mapping in config.yaml.
    """
    user_mapping = _app_state.get("user_mapping", {})
    default_user = _app_state.get("default_user", "")

    # Check for Home Assistant user ID header
    ha_user_id = request.headers.get("X-Hass-User-Id")
    if ha_user_id and ha_user_id in user_mapping:
        return user_mapping[ha_user_id]

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

    if template_name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    template = templates[template_name]

    # Parse form data directly from request
    form_raw = await request.form()
    form_data: dict[str, Any] = {}
    for key, value in form_raw.items():
        form_data[key] = value

    # Extract printer (from query param or form) and quantity
    printer_name = printer or form_data.pop("printer", None)
    quantity = int(form_data.pop("quantity", 1))

    # Populate USER fields from request context
    from labelable.models.template import FieldType

    current_user = _resolve_user(request)
    for field in template.fields:
        if field.type == FieldType.USER:
            form_data[field.name] = current_user

    # Add quantity to context for template use (e.g., "1 of {{ quantity }}")
    form_data["quantity"] = quantity

    if not printer_name or printer_name not in printers:
        return _page_wrapper(
            c.Heading(text="Error", level=2),
            c.Paragraph(text="Invalid printer selected."),
            c.Link(
                components=[c.Text(text="<- Try again")],
                on_click=GoToEvent(url=f"/print/{template_name}"),
            ),
        )

    printer = printers[printer_name]

    # Render template
    try:
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

    is_online = await printer.is_online()
    await queue.submit(job)

    if is_online:
        message = f"Label sent to printer '{printer_name}'."
    else:
        message = f"Label queued. Printer '{printer_name}' is currently offline."

    return _page_wrapper(
        c.Heading(text="Print Submitted", level=2),
        c.Paragraph(text=message),
        c.Paragraph(text=f"Job ID: {job.id}"),
        c.Paragraph(text=f"Quantity: {quantity}"),
        c.Link(
            components=[c.Text(text="Print another")],
            on_click=GoToEvent(url=f"/print/{template_name}"),
        ),
        c.Link(
            components=[c.Text(text="<- Back to templates")],
            on_click=GoToEvent(url="/"),
        ),
        title="Print Submitted - Labelable",
    )


def _create_form_model(template, compatible_printers: list[tuple[str, str]] | None) -> type[BaseModel]:
    """Create a dynamic Pydantic model for the template form.

    Args:
        template: The template configuration
        compatible_printers: List of (name, display) tuples for printer dropdown,
            or None if printer is passed via query param (single printer case)
    """
    from enum import Enum

    from labelable.models.template import FieldType

    fields: dict[str, Any] = {}

    # Only show printer dropdown if multiple printers available
    if compatible_printers is not None and len(compatible_printers) > 1:
        PrinterEnum = Enum("PrinterEnum", {name: name for name, _ in compatible_printers})
        fields["printer"] = (PrinterEnum, Field(title="Printer"))
    # If compatible_printers is None, printer comes from query param - no field needed

    # Quantity field
    fields["quantity"] = (int, Field(default=1, ge=1, title="Quantity"))

    # Template fields
    for field in template.fields:
        # Skip auto-populated fields
        if field.type == FieldType.DATETIME:
            continue  # Auto-populates with current time
        if field.type == FieldType.USER:
            continue  # Auto-populates from request context

        field_type: type
        if field.type == FieldType.INTEGER:
            field_type = int
        elif field.type == FieldType.FLOAT:
            field_type = float
        elif field.type == FieldType.BOOLEAN:
            field_type = bool
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
        if field.required and field.default is None:
            title = f"{base_title} *"
            fields[field.name] = (
                field_type,
                Field(title=title, description=field.description or None),
            )
        else:
            fields[field.name] = (
                field_type | None,
                Field(
                    default=field.default,
                    title=base_title,
                    description=field.description or None,
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
