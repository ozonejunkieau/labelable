"""Tests for print-related API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from labelable.api import routes as api_routes
from labelable.app import create_app
from labelable.models.job import JobStatus, PrintJob
from labelable.models.template import EngineType, FieldType, LabelDimensions, TemplateConfig, TemplateField
from labelable.queue import PrintQueue
from labelable.templates.jinja_engine import JinjaTemplateEngine


def _make_mock_printer(name="test-printer", online=True):
    printer = MagicMock()
    printer.name = name
    printer.config.type.value = "zpl"
    printer.config.type = MagicMock()
    printer.config.type.value = "zpl"
    printer.is_online = AsyncMock(return_value=online)
    printer.last_checked = None
    return printer


def _make_jinja_template(name="test-template", supported_printers=None, template_str="^XA^FD{{ name }}^FS^XZ"):
    if supported_printers is None:
        supported_printers = ["test-printer"]
    return TemplateConfig(
        name=name,
        description="Test template",
        dimensions=LabelDimensions(width_mm=100, height_mm=50),
        supported_printers=supported_printers,
        fields=[TemplateField(name="name", type=FieldType.STRING, required=True)],
        template=template_str,
    )


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset app state before each test."""
    api_routes._app_state.clear()
    yield
    api_routes._app_state.clear()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def queue():
    return PrintQueue()


@pytest.fixture
def jinja_engine():
    return JinjaTemplateEngine()


class TestPrintLabel:
    def _setup_state(self, printers, templates, queue, jinja_engine, image_engine=None):
        api_routes.set_app_state(printers, templates, queue, jinja_engine, image_engine)

    def test_template_not_found(self, client, queue, jinja_engine):
        self._setup_state({}, {}, queue, jinja_engine)
        response = client.post("/api/v1/print/nonexistent", json={"data": {}})
        assert response.status_code == 404
        assert "nonexistent" in response.json()["detail"]

    def test_no_compatible_printer(self, client, queue, jinja_engine):
        template = _make_jinja_template(supported_printers=["other-printer"])
        printer = _make_mock_printer("test-printer")
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        response = client.post("/api/v1/print/test-template", json={"data": {"name": "hi"}})
        assert response.status_code == 400
        assert "No printer specified" in response.json()["detail"]

    def test_printer_not_found(self, client, queue, jinja_engine):
        template = _make_jinja_template(supported_printers=["missing"])
        self._setup_state({}, {"test-template": template}, queue, jinja_engine)
        response = client.post("/api/v1/print/test-template", json={"printer": "missing", "data": {"name": "hi"}})
        assert response.status_code == 404
        assert "missing" in response.json()["detail"]

    def test_printer_not_in_supported_list(self, client, queue, jinja_engine):
        template = _make_jinja_template(supported_printers=["other"])
        printer = _make_mock_printer("test-printer")
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        response = client.post(
            "/api/v1/print/test-template",
            json={"printer": "test-printer", "data": {"name": "hi"}},
        )
        assert response.status_code == 400
        assert "not in template's supported_printers" in response.json()["detail"]

    def test_render_error(self, client, queue, jinja_engine):
        template = _make_jinja_template(template_str="{{ unclosed")
        printer = _make_mock_printer()
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        response = client.post("/api/v1/print/test-template", json={"data": {"name": "hi"}})
        assert response.status_code == 400
        assert "rendering failed" in response.json()["detail"].lower()

    def test_successful_print_online_printer(self, client, queue, jinja_engine):
        template = _make_jinja_template()
        printer = _make_mock_printer(online=True)
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        response = client.post("/api/v1/print/test-template", json={"data": {"name": "World"}})
        assert response.status_code == 200
        body = response.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_successful_print_offline_printer(self, client, queue, jinja_engine):
        template = _make_jinja_template()
        printer = _make_mock_printer(online=False)
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        response = client.post("/api/v1/print/test-template", json={"data": {"name": "World"}})
        assert response.status_code == 202
        # 202 is raised as HTTPException so the body is wrapped under "detail"
        detail = response.json()["detail"]
        assert "job_id" in detail

    def test_auto_selects_compatible_printer(self, client, queue, jinja_engine):
        template = _make_jinja_template(supported_printers=["test-printer"])
        printer = _make_mock_printer("test-printer")
        self._setup_state({"test-printer": printer}, {"test-template": template}, queue, jinja_engine)
        # No printer specified in request body
        response = client.post("/api/v1/print/test-template", json={"data": {"name": "hi"}})
        assert response.status_code == 200

    def test_image_engine_path(self, client, queue, jinja_engine):
        template = TemplateConfig(
            name="img-template",
            description="Image template",
            engine=EngineType.IMAGE,
            dimensions=LabelDimensions(width_mm=50, height_mm=25),
            supported_printers=["test-printer"],
            fields=[TemplateField(name="name", type=FieldType.STRING, required=True)],
        )
        printer = _make_mock_printer(online=True)
        image_engine = MagicMock()
        image_engine.render = MagicMock(return_value=b"fake-image-data")
        self._setup_state(
            {"test-printer": printer},
            {"img-template": template},
            queue,
            jinja_engine,
            image_engine=image_engine,
        )
        response = client.post("/api/v1/print/img-template", json={"data": {"name": "Test"}})
        assert response.status_code == 200
        image_engine.render.assert_called_once()


class TestGetJobStatus:
    def _setup_state(self, queue, jinja_engine):
        api_routes.set_app_state({}, {}, queue, jinja_engine)

    def test_job_not_found(self, client, queue, jinja_engine):
        self._setup_state(queue, jinja_engine)
        response = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_job_found(self, client, queue, jinja_engine):
        self._setup_state(queue, jinja_engine)
        job = PrintJob(
            template_name="test-template",
            printer_name="test-printer",
            data={"name": "hi"},
            status=JobStatus.COMPLETED,
        )
        queue._jobs[str(job.id)] = job
        response = client.get(f"/api/v1/jobs/{job.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == str(job.id)
        assert body["status"] == "completed"


class TestApiKeyAuth:
    """Tests for verify_api_key dependency."""

    def _setup_state_with_key(self, queue, jinja_engine, api_key=None):
        api_routes.set_app_state({}, {}, queue, jinja_engine, api_key=api_key)

    def test_no_key_configured_allows_access(self, client, queue, jinja_engine):
        self._setup_state_with_key(queue, jinja_engine, api_key=None)
        response = client.get("/api/v1/printers")
        assert response.status_code == 200

    def test_bearer_token_accepted(self, client, queue, jinja_engine):
        self._setup_state_with_key(queue, jinja_engine, api_key="secret123")
        response = client.get("/api/v1/printers", headers={"Authorization": "Bearer secret123"})
        assert response.status_code == 200

    def test_query_param_accepted(self, client, queue, jinja_engine):
        self._setup_state_with_key(queue, jinja_engine, api_key="secret123")
        response = client.get("/api/v1/printers?api_key=secret123")
        assert response.status_code == 200

    def test_wrong_key_rejected(self, client, queue, jinja_engine):
        self._setup_state_with_key(queue, jinja_engine, api_key="secret123")
        response = client.get("/api/v1/printers", headers={"Authorization": "Bearer wrongkey"})
        assert response.status_code == 401

    def test_ingress_path_bypasses_auth(self, client, queue, jinja_engine):
        self._setup_state_with_key(queue, jinja_engine, api_key="secret123")
        response = client.get("/api/v1/printers", headers={"X-Ingress-Path": "/hassio_ingress/abc123"})
        assert response.status_code == 200
