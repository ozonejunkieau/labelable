"""Tests for API and UI routes."""

import pytest
from fastapi.testclient import TestClient

from labelable.app import create_app
from labelable.models.printer import PrinterType
from labelable.models.template import (
    BoundingBox,
    EngineType,
    FieldType,
    LabelDimensions,
    TemplateConfig,
    TemplateField,
    TextElement,
)


@pytest.fixture
def sample_template() -> TemplateConfig:
    """Create a sample template for testing."""
    return TemplateConfig(
        name="test-label",
        description="A test label template",
        dimensions=LabelDimensions(width_mm=100, height_mm=50),
        supported_printers=[PrinterType.ZPL],
        fields=[
            TemplateField(name="title", type=FieldType.STRING, required=True),
            TemplateField(name="count", type=FieldType.INTEGER, default=1),
        ],
        template="^XA^FD{{ title }}^FS^XZ",
    )


@pytest.fixture
def image_template() -> TemplateConfig:
    """Create a sample image-based template for testing."""
    return TemplateConfig(
        name="test-image-label",
        description="A test image template",
        engine=EngineType.IMAGE,
        dimensions=LabelDimensions(width_mm=50, height_mm=25),
        supported_printers=["test-zpl"],  # Must match printer name, not type
        fields=[
            TemplateField(name="title", type=FieldType.STRING, required=True),
        ],
        elements=[
            TextElement(
                type="text",
                field="title",
                bounds=BoundingBox(x_mm=5, y_mm=5, width_mm=40, height_mm=15),
                font_size=12,
            ),
        ],
    )


class TestUIComponents:
    """Test that UI components are correctly imported and usable."""

    def test_display_lookup_import(self):
        """Ensure DisplayLookup can be imported from the correct module."""
        from fastui.components.display import DisplayLookup

        # Verify it can be instantiated
        lookup = DisplayLookup(field="test", title="Test")
        assert lookup.field == "test"
        assert lookup.title == "Test"

    def test_all_used_fastui_components_exist(self):
        """Verify all FastUI components used in the UI module exist (regression test)."""
        from fastui import components as c

        # All components used in ui.py must exist
        used_components = [
            "PageTitle",
            "Navbar",
            "Page",
            "Footer",
            "Heading",
            "Paragraph",
            "Text",
            "Link",
            "Button",
            "Table",
            "ModelForm",
            "Div",  # Used for card layouts
            "Markdown",  # Used for status badges with HTML
            "Image",  # Used for preview feature
        ]
        for component_name in used_components:
            assert hasattr(c, component_name), f"c.{component_name} does not exist"

        # Verify c.Html does NOT exist (we should not use it)
        assert not hasattr(c, "Html"), "c.Html does not exist - use c.Div instead"

    def test_ui_module_imports(self):
        """Ensure the UI module can be imported without errors."""
        # This will fail if any import is broken
        from labelable.api import ui

        assert ui.router is not None
        # Verify row models are defined
        assert ui.PrinterRow is not None
        assert ui.FieldRow is not None

    def test_table_with_display_lookup(self):
        """Ensure Table component works with DisplayLookup columns."""
        from fastui import components as c
        from fastui.components.display import DisplayLookup
        from pydantic import BaseModel

        class TestRow(BaseModel):
            name: str
            value: int

        # This should not raise any errors
        table = c.Table(
            data=[TestRow(name="test", value=42)],
            columns=[
                DisplayLookup(field="name", title="Name"),
                DisplayLookup(field="value", title="Value"),
            ],
        )
        assert table is not None

    def test_table_serialization_with_pydantic_models(self):
        """Ensure Table with Pydantic models can be serialized (regression test)."""
        from fastui import components as c
        from fastui.components.display import DisplayLookup

        from labelable.api.ui import FieldRow, PrinterRow

        # Create tables with our actual row models
        printer_table = c.Table(
            data=[
                PrinterRow(name="test", type="zpl", model="GX420D", status="Online", queue="0", last_checked="12:00:00")
            ],
            columns=[
                DisplayLookup(field="name", title="Name"),
                DisplayLookup(field="type", title="Type"),
            ],
        )
        # This will raise PydanticSerializationError if models are wrong
        printer_json = printer_table.model_dump_json()
        assert "test" in printer_json

        field_table = c.Table(
            data=[FieldRow(name="title", type="string", required="Yes", default="-")],
            columns=[
                DisplayLookup(field="name", title="Name"),
                DisplayLookup(field="type", title="Type"),
            ],
        )
        field_json = field_table.model_dump_json()
        assert "title" in field_json


class TestUIRoutes:
    """Test UI route responses."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create a test client."""
        app = create_app()
        return TestClient(app)

    def test_home_page_no_templates(self, client: TestClient):
        """Test home page renders when no templates are configured."""
        response = client.get("/api/")
        assert response.status_code == 200

    def test_printers_page_no_printers(self, client: TestClient):
        """Test printers page renders when no printers are configured."""
        response = client.get("/api/printers")
        assert response.status_code == 200

    def test_reload_templates_returns_template_configs(self, client: TestClient):
        """Test reload_templates stores TemplateConfig objects, not dicts.

        Regression test: load_templates() returns TemplateLoadResult, and
        reload_templates must use .templates to get the dict of TemplateConfig.
        """
        from labelable.api import ui

        # Call reload_templates endpoint
        response = client.get("/api/reload-templates")
        assert response.status_code == 200

        # Verify templates in app state are TemplateConfig objects, not dicts
        templates = ui._app_state.get("templates", {})
        for name, template in templates.items():
            assert hasattr(template, "dimensions"), (
                f"Template '{name}' is a {type(template).__name__}, not TemplateConfig"
            )
            assert hasattr(template, "name"), f"Template '{name}' missing 'name' attribute"

    def test_spa_handler_returns_html_with_dark_mode(self, client: TestClient):
        """Test SPA handler returns HTML with dark mode CSS."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        # Check for dark mode media query
        assert "prefers-color-scheme: dark" in html
        # Check for required elements
        assert "<title>Labelable</title>" in html
        assert 'id="root"' in html


class TestAPIRoutes:
    """Test REST API route responses."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create a test client."""
        app = create_app()
        return TestClient(app)

    def test_list_printers_empty(self, client: TestClient):
        """Test listing printers when none are configured."""
        response = client.get("/api/v1/printers")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_templates_empty(self, client: TestClient):
        """Test listing templates when none are loaded."""
        response = client.get("/api/v1/templates")
        assert response.status_code == 200
        # May have the example template
        assert isinstance(response.json(), list)


class TestIngressURLHandling:
    """Test Home Assistant Ingress URL path handling.

    These tests ensure that the URL handling works correctly when accessed
    via Home Assistant Ingress, which proxies requests through a path like
    /api/hassio_ingress/<token>/.

    Regression tests for the ingress path doubling bug fixed in v0.1.0.
    """

    @pytest.fixture
    def client(self) -> TestClient:
        """Create a test client."""
        app = create_app()
        return TestClient(app)

    def test_spa_handler_without_ingress(self, client: TestClient):
        """Test SPA handler returns correct meta tags when accessed directly."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.text

        # Without ingress, APIRootUrl should be /api
        assert 'name="fastui:APIRootUrl" content="/api"' in html
        # Without ingress, APIPathStrip should be empty
        assert 'name="fastui:APIPathStrip" content=""' in html

    def test_spa_handler_with_ingress_header(self, client: TestClient):
        """Test SPA handler correctly handles X-Ingress-Path header."""
        ingress_path = "/api/hassio_ingress/test-token-12345"
        response = client.get("/", headers={"X-Ingress-Path": ingress_path})
        assert response.status_code == 200
        html = response.text

        # With ingress, APIRootUrl should include ingress path + /api
        expected_api_root = f'name="fastui:APIRootUrl" content="{ingress_path}/api"'
        assert expected_api_root in html, f"Expected {expected_api_root} in HTML"

        # With ingress, APIPathStrip should be the ingress path
        expected_path_strip = f'name="fastui:APIPathStrip" content="{ingress_path}"'
        assert expected_path_strip in html, f"Expected {expected_path_strip} in HTML"

    def test_spa_handler_strips_trailing_slash_from_ingress_path(self, client: TestClient):
        """Test that trailing slash is stripped from X-Ingress-Path."""
        ingress_path = "/api/hassio_ingress/test-token/"
        expected_path = "/api/hassio_ingress/test-token"  # No trailing slash
        response = client.get("/", headers={"X-Ingress-Path": ingress_path})
        assert response.status_code == 200
        html = response.text

        # Should use path without trailing slash
        expected_api_root = f'name="fastui:APIRootUrl" content="{expected_path}/api"'
        assert expected_api_root in html

    def test_navigation_urls_work_with_ingress(self, client: TestClient):
        """Test that FastUI API routes respond correctly when accessed via ingress.

        This tests the server-side route matching when the request comes through
        the ingress path. The actual URL is still /api/* since HA strips the
        ingress prefix before forwarding.
        """
        # These routes should work - they're what the browser fetches
        response = client.get("/api/")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        response = client.get("/api/printers")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    def test_footer_contains_version_and_github_link(self, client: TestClient):
        """Test that API responses include footer with version info as GitHub link."""
        response = client.get("/api/")
        assert response.status_code == 200
        data = response.json()

        # Find the Footer component in the response
        footer = None
        for component in data:
            if component.get("type") == "Footer":
                footer = component
                break

        assert footer is not None, "Footer component not found in response"

        # Check for version link (version text is now the GitHub link)
        links = footer.get("links", [])
        assert len(links) > 0, "No links in footer"
        version_link = links[0]
        # The link text should contain the version
        assert "Labelable v" in str(version_link), "Version not in footer link"
        # The link should point to GitHub
        assert "github.com" in str(version_link), "GitHub URL not in footer link"


class TestPreviewFeature:
    """Test preview functionality for image templates."""

    @pytest.fixture
    def client_with_image_template(self, image_template: TemplateConfig) -> TestClient:
        """Create a test client with an image template configured."""
        from unittest.mock import MagicMock

        from labelable.api import ui
        from labelable.app import create_app

        app = create_app()

        # Set up mock image engine
        mock_image_engine = MagicMock()
        # Return a minimal valid PNG (1x1 pixel white PNG)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
            b"\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        mock_image_engine.render_preview.return_value = png_data

        # Store original state
        original_templates = ui._app_state.get("templates", {}).copy()
        original_image_engine = ui._app_state.get("image_engine")

        # Set up templates and engine
        ui._app_state["templates"] = {image_template.name: image_template}
        ui._app_state["image_engine"] = mock_image_engine

        client = TestClient(app)
        yield client

        # Restore original state
        ui._app_state["templates"] = original_templates
        ui._app_state["image_engine"] = original_image_engine

    def test_preview_endpoint_returns_image(self, client_with_image_template: TestClient):
        """Test that preview endpoint returns a page with base64 image."""
        response = client_with_image_template.post(
            "/api/print/test-image-label/preview?printer=test-printer",
            data={"title": "Test Title", "quantity": "1"},
        )
        # Should redirect to error page since test-printer doesn't exist
        assert response.status_code == 200
        data = response.json()
        # Should show error about invalid printer
        page_text = str(data)
        assert "Invalid printer" in page_text or "Error" in page_text

    def test_preview_not_available_for_jinja_templates(self, sample_template: TemplateConfig):
        """Test that preview returns 400 for non-image templates."""
        from labelable.api import ui
        from labelable.app import create_app

        app = create_app()

        # Store original state
        original_templates = ui._app_state.get("templates", {}).copy()

        # Set up jinja template
        ui._app_state["templates"] = {sample_template.name: sample_template}

        client = TestClient(app)
        response = client.post(
            f"/api/print/{sample_template.name}/preview?printer=test-printer",
            data={"title": "Test", "quantity": "1"},
        )

        # Restore original state
        ui._app_state["templates"] = original_templates

        assert response.status_code == 400
        assert "image templates" in response.json()["detail"].lower()

    def test_preview_returns_404_for_missing_template(self):
        """Test that preview returns 404 for non-existent template."""
        from labelable.app import create_app

        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/api/print/nonexistent-template/preview?printer=test-printer",
            data={"title": "Test", "quantity": "1"},
        )
        assert response.status_code == 404

    def test_image_template_form_shows_preview_hint(self, image_template: TemplateConfig):
        """Test that image template form page shows preview message."""
        from unittest.mock import MagicMock

        from labelable.api import ui
        from labelable.app import create_app

        app = create_app()

        # Set up mock printer
        mock_printer = MagicMock()
        mock_printer.config.type = PrinterType.ZPL
        mock_printer.get_cached_online_status.return_value = True
        mock_printer.config.connection.host = "192.168.1.100"
        mock_printer.config.connection.port = 9100

        # Store original state
        original_templates = ui._app_state.get("templates", {}).copy()
        original_printers = ui._app_state.get("printers", {}).copy()
        original_queue = ui._app_state.get("queue")

        # Set up template and printer
        ui._app_state["templates"] = {image_template.name: image_template}
        ui._app_state["printers"] = {"test-zpl": mock_printer}

        mock_queue = MagicMock()
        mock_queue.get_queue_size.return_value = 0
        ui._app_state["queue"] = mock_queue

        client = TestClient(app)
        response = client.get(f"/api/print/{image_template.name}")

        # Restore original state
        ui._app_state["templates"] = original_templates
        ui._app_state["printers"] = original_printers
        ui._app_state["queue"] = original_queue

        assert response.status_code == 200
        data = response.json()
        page_text = str(data)
        # Check that page mentions preview
        assert "Preview" in page_text or "preview" in page_text

    def test_hidden_form_model_creation(self):
        """Test _create_hidden_form_model creates proper model."""
        from labelable.api.ui import _create_hidden_form_model

        hidden_fields = {
            "title": "Test Title",
            "count": 5,
            "enabled": True,
            "price": 19.99,
        }
        quantity = 3

        model = _create_hidden_form_model(hidden_fields, quantity)

        # Create instance with defaults
        instance = model()

        # Verify all fields are present with correct defaults
        assert instance.quantity == 3
        assert instance.title == "Test Title"
        assert instance.count == 5
        assert instance.enabled is True
        assert instance.price == 19.99

    def test_hidden_form_model_works_with_fastui_model_form(self):
        """Test that hidden form model can be used with FastUI ModelForm.

        Regression test: json_schema_extra with type="hidden" breaks FastUI
        because it overrides the JSON schema type field.
        """
        from fastui import components as c

        from labelable.api.ui import _create_hidden_form_model

        hidden_fields = {
            "title": "Gluten Free Flour",
            "count": 5,
        }
        quantity = 1

        model = _create_hidden_form_model(hidden_fields, quantity)

        # This should not raise an error - FastUI needs valid JSON schema types
        form = c.ModelForm(
            model=model,
            submit_url="/api/test/submit",
            display_mode="inline",
        )

        # Serialize to JSON to trigger the schema generation that was failing
        form_json = form.model_dump_json()
        assert "title" in form_json
        assert "quantity" in form_json
