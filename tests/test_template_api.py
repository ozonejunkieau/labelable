"""Tests for template CRUD REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

from labelable.api import routes as api_routes
from labelable.app import create_app
from labelable.models.template import LabelDimensions, TemplateConfig


def _template_json(name: str = "test-label", **overrides) -> dict:
    base = {
        "name": name,
        "description": "A test template",
        "dimensions": {"width_mm": 40, "height_mm": 28},
        "supported_printers": ["zpl"],
    }
    base.update(overrides)
    return base


class TestCreateTemplate:
    """Test POST /api/v1/templates endpoint."""

    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        app = create_app()
        client = TestClient(app)
        # Set up templates_path in app state
        api_routes._app_state["templates_path"] = tmp_path
        yield client

    def test_create_success(self, client: TestClient, tmp_path):
        response = client.post("/api/v1/templates", json=_template_json())
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-label"
        assert data["status"] == "created"

        # Verify template exists in state
        templates = api_routes._app_state.get("templates", {})
        assert "test-label" in templates

        # Verify file was written
        assert (tmp_path / "test-label.yaml").exists()

        # Cleanup
        templates.pop("test-label", None)

    def test_create_duplicate_returns_409(self, client: TestClient):
        # Create first
        client.post("/api/v1/templates", json=_template_json("dup-test"))

        # Create duplicate
        response = client.post("/api/v1/templates", json=_template_json("dup-test"))
        assert response.status_code == 409

        # Cleanup
        templates = api_routes._app_state.get("templates", {})
        templates.pop("dup-test", None)

    def test_create_invalid_name_no_file_written(self, client: TestClient, tmp_path):
        """Reject invalid names and verify nothing is written to disk."""
        response = client.post("/api/v1/templates", json=_template_json("../evil"))
        assert response.status_code == 400

        # No files should exist in the templates directory
        assert list(tmp_path.iterdir()) == []

    def test_create_invalid_schema_no_file_written(self, client: TestClient, tmp_path):
        """Reject invalid schema and verify nothing is written to disk."""
        response = client.post("/api/v1/templates", json={"invalid": "data"})
        assert response.status_code == 422

        assert list(tmp_path.iterdir()) == []

    def test_create_path_traversal_no_escape(self, client: TestClient, tmp_path):
        """Verify path traversal names cannot write outside templates dir."""
        for name in ["__proto__", "..%2Fevil", "a/../../etc"]:
            response = client.post("/api/v1/templates", json=_template_json(name))
            assert response.status_code in (400, 422), f"Name '{name}' was not rejected"

        # Nothing written anywhere in tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_created_template_appears_in_list(self, client: TestClient):
        client.post("/api/v1/templates", json=_template_json("list-test"))

        response = client.get("/api/v1/templates")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()]
        assert "list-test" in names

        # Cleanup
        templates = api_routes._app_state.get("templates", {})
        templates.pop("list-test", None)

    def test_api_key_enforcement(self, tmp_path):
        """Test that API key is required when configured."""
        app = create_app()
        client = TestClient(app)
        api_routes._app_state["templates_path"] = tmp_path
        api_routes._app_state["api_key"] = "test-secret-key"

        # Without key
        response = client.post("/api/v1/templates", json=_template_json("key-test"))
        assert response.status_code == 401

        # With key
        response = client.post(
            "/api/v1/templates",
            json=_template_json("key-test"),
            headers={"X-API-Key": "test-secret-key"},
        )
        assert response.status_code == 201

        # Cleanup
        templates = api_routes._app_state.get("templates", {})
        templates.pop("key-test", None)
        api_routes._app_state["api_key"] = None


class TestUpdateTemplate:
    """Test PUT /api/v1/templates/{name} endpoint."""

    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        app = create_app()
        client = TestClient(app)
        api_routes._app_state["templates_path"] = tmp_path

        # Pre-create a template
        template = TemplateConfig(
            name="existing",
            description="Original",
            dimensions=LabelDimensions(width_mm=40, height_mm=28),
            supported_printers=["zpl"],
        )
        templates = api_routes._app_state.setdefault("templates", {})
        templates["existing"] = template
        # Also write the file
        from labelable.api.template_crud import template_to_yaml

        (tmp_path / "existing.yaml").write_text(template_to_yaml(template))

        yield client

        # Cleanup
        templates.pop("existing", None)

    def test_update_success(self, client: TestClient):
        updated = _template_json("existing", description="Updated")
        response = client.put("/api/v1/templates/existing", json=updated)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"

        templates = api_routes._app_state.get("templates", {})
        assert templates["existing"].description == "Updated"

    def test_update_not_found_returns_404(self, client: TestClient):
        response = client.put(
            "/api/v1/templates/nonexistent",
            json=_template_json("nonexistent"),
        )
        assert response.status_code == 404
