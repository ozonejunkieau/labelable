"""Tests for MCP server tools."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

mcp_mod = pytest.importorskip("mcp")

from labelable.api.mcp_server import create_mcp_server, set_app_state  # noqa: E402
from labelable.models.template import LabelDimensions, TemplateConfig, TemplateField  # noqa: E402


def _make_template(name: str = "test-label") -> TemplateConfig:
    return TemplateConfig(
        name=name,
        description="A test template",
        dimensions=LabelDimensions(width_mm=40, height_mm=28),
        supported_printers=["zpl"],
        fields=[TemplateField(name="title", type="string", required=True)],
    )


def _mock_printer(name: str = "zpl", printer_type: str = "zpl", online: bool = True):
    printer = AsyncMock()
    printer.name = name
    printer.config = MagicMock()
    printer.config.type = MagicMock()
    printer.config.type.value = printer_type
    printer.is_online = AsyncMock(return_value=online)
    printer.last_checked = None
    return printer


@pytest.fixture
def setup_state(tmp_path: Path):
    """Set up MCP app state with mock printers and templates."""
    template = _make_template()
    templates = {"test-label": template}
    printer = _mock_printer()
    printers = {"zpl": printer}
    queue = MagicMock()
    queue.get_queue_size = MagicMock(return_value=0)
    queue.submit = AsyncMock()
    jinja_engine = MagicMock()
    jinja_engine.render = MagicMock(return_value=b"^XA^FDTest^FS^XZ")

    set_app_state(
        printers=printers,
        templates=templates,
        queue=queue,
        jinja_engine=jinja_engine,
        templates_path=tmp_path,
    )

    return {
        "printers": printers,
        "templates": templates,
        "queue": queue,
        "jinja_engine": jinja_engine,
        "tmp_path": tmp_path,
    }


@pytest.fixture
def mcp_server(setup_state):
    """Create an MCP server instance."""
    return create_mcp_server()


class TestListPrinters:
    async def test_returns_printer_list(self, mcp_server, setup_state):
        # Call the tool function directly via the server's tool registry
        result = await _call_tool(mcp_server, "list_printers", {})
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "zpl"
        assert data[0]["online"] is True
        assert data[0]["queue_size"] == 0


class TestListTemplates:
    async def test_returns_template_list(self, mcp_server, setup_state):
        result = await _call_tool(mcp_server, "list_templates", {})
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "test-label"
        assert len(data[0]["fields"]) == 1


class TestGetTemplate:
    async def test_returns_template(self, mcp_server, setup_state):
        result = await _call_tool(mcp_server, "get_template", {"name": "test-label"})
        data = json.loads(result)
        assert data["name"] == "test-label"
        assert data["description"] == "A test template"

    async def test_not_found(self, mcp_server, setup_state):
        result = await _call_tool(mcp_server, "get_template", {"name": "nonexistent"})
        data = json.loads(result)
        assert "error" in data


class TestPrintLabel:
    async def test_print_success(self, mcp_server, setup_state):
        result = await _call_tool(
            mcp_server,
            "print_label",
            {
                "template_name": "test-label",
                "data": {"title": "Hello"},
                "printer": "zpl",
            },
        )
        data = json.loads(result)
        assert "job_id" in data
        assert data["message"] == "Label submitted for printing"
        setup_state["queue"].submit.assert_called_once()

    async def test_template_not_found(self, mcp_server, setup_state):
        result = await _call_tool(
            mcp_server,
            "print_label",
            {
                "template_name": "nonexistent",
                "data": {},
            },
        )
        data = json.loads(result)
        assert "error" in data

    async def test_printer_not_found(self, mcp_server, setup_state):
        result = await _call_tool(
            mcp_server,
            "print_label",
            {
                "template_name": "test-label",
                "data": {"title": "Hello"},
                "printer": "nonexistent",
            },
        )
        data = json.loads(result)
        assert "error" in data


class TestCreateTemplate:
    async def test_create_success(self, mcp_server, setup_state):
        template_data = {
            "name": "new-label",
            "description": "Brand new",
            "dimensions": {"width_mm": 50, "height_mm": 30},
            "supported_printers": ["zpl"],
        }
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": json.dumps(template_data),
            },
        )
        data = json.loads(result)
        assert data["status"] == "created"
        assert data["name"] == "new-label"

        # Verify in templates dict
        assert "new-label" in setup_state["templates"]

        # Verify file on disk
        assert (setup_state["tmp_path"] / "new-label.yaml").exists()

    async def test_create_duplicate(self, mcp_server, setup_state):
        template_data = {
            "name": "test-label",
            "dimensions": {"width_mm": 40, "height_mm": 28},
        }
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": json.dumps(template_data),
            },
        )
        data = json.loads(result)
        assert "error" in data
        assert "already exists" in data["error"]

    async def test_create_invalid_json_no_file_written(self, mcp_server, setup_state):
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": "not json",
            },
        )
        data = json.loads(result)
        assert "error" in data

        # No file should be written
        assert list(setup_state["tmp_path"].iterdir()) == []

    async def test_create_invalid_schema_no_file_written(self, mcp_server, setup_state):
        """Valid JSON but missing required fields should not write to disk."""
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": json.dumps({"not_a_template": True}),
            },
        )
        data = json.loads(result)
        assert "error" in data
        assert "Invalid template schema" in data["error"]

        assert list(setup_state["tmp_path"].iterdir()) == []

    async def test_create_path_traversal_no_file_written(self, mcp_server, setup_state):
        """Path traversal names should be rejected before writing."""
        template_data = {
            "name": "../evil",
            "dimensions": {"width_mm": 40, "height_mm": 28},
        }
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": json.dumps(template_data),
            },
        )
        data = json.loads(result)
        assert "error" in data

        assert list(setup_state["tmp_path"].iterdir()) == []

    async def test_create_duplicate_no_extra_file(self, mcp_server, setup_state):
        """Duplicate create should not write a second file."""
        template_data = {
            "name": "test-label",
            "dimensions": {"width_mm": 40, "height_mm": 28},
        }
        result = await _call_tool(
            mcp_server,
            "create_template",
            {
                "template_json": json.dumps(template_data),
            },
        )
        data = json.loads(result)
        assert "error" in data

        # No file should exist (the original was only in-memory, not on disk)
        assert list(setup_state["tmp_path"].iterdir()) == []


class TestUpdateTemplate:
    async def test_update_success(self, mcp_server, setup_state):
        updated = {
            "name": "test-label",
            "description": "Updated",
            "dimensions": {"width_mm": 40, "height_mm": 28},
            "supported_printers": ["zpl"],
        }
        result = await _call_tool(
            mcp_server,
            "update_template",
            {
                "name": "test-label",
                "template_json": json.dumps(updated),
            },
        )
        data = json.loads(result)
        assert data["status"] == "updated"
        assert setup_state["templates"]["test-label"].description == "Updated"

    async def test_update_not_found(self, mcp_server, setup_state):
        updated = {
            "name": "nonexistent",
            "dimensions": {"width_mm": 40, "height_mm": 28},
        }
        result = await _call_tool(
            mcp_server,
            "update_template",
            {
                "name": "nonexistent",
                "template_json": json.dumps(updated),
            },
        )
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"]


async def _call_tool(mcp_server, tool_name: str, arguments: dict) -> str:
    """Call an MCP tool directly and return the text result."""
    result = await mcp_server.call_tool(tool_name, arguments)
    # FastMCP call_tool returns (content_list, metadata) tuple
    content_list = result[0]
    assert len(content_list) > 0
    return content_list[0].text
