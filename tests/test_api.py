"""Tests for API and UI routes."""

import pytest
from fastapi.testclient import TestClient

from labelable.app import create_app
from labelable.models.printer import PrinterType
from labelable.models.template import (
    FieldType,
    LabelDimensions,
    TemplateConfig,
    TemplateField,
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
