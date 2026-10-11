"""UI print form: what the browser submits must be what gets printed.

These tests drive preview -> print the way a browser does: they read the
hidden form FastUI is told to render, build the FormData a browser would
post from it, and check what reaches the queue.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from labelable.api import ui
from labelable.app import create_app
from labelable.models.template import EngineType, FieldType, LabelDimensions, TemplateConfig, TemplateField

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _template(**overrides: Any) -> TemplateConfig:
    config: dict[str, Any] = {
        "name": "batch",
        "engine": EngineType.IMAGE,
        "dimensions": LabelDimensions(width_mm=9, height_mm=50),
        "supported_printers": ["p1"],
        "fields": [
            TemplateField(name="signals", type=FieldType.LIST, required=True),
            TemplateField(name="bold", type=FieldType.BOOLEAN, default=True),
            TemplateField(name="copies_note", type=FieldType.INTEGER, required=False),
        ],
    }
    config.update(overrides)
    return TemplateConfig(**config)


@pytest.fixture
def ui_state():
    printer = MagicMock()
    printer.output_format = "zpl"
    printer.dpi = None
    printer.is_online = AsyncMock(return_value=True)

    engine = MagicMock()
    engine.render_preview.return_value = PNG_1X1
    engine.render.return_value = b"^XA^XZ"

    queue = MagicMock()
    queue.submit = AsyncMock()
    queue.get_queue_size.return_value = 0

    saved = dict(ui._app_state)
    ui._app_state.update(
        templates={"batch": _template()},
        printers={"p1": printer, "other": printer},
        image_engine=engine,
        jinja_engine=MagicMock(),
        queue=queue,
    )
    yield {"engine": engine, "queue": queue}
    ui._app_state.clear()
    ui._app_state.update(saved)


@pytest.fixture
def client(ui_state) -> TestClient:
    return TestClient(create_app())


def _find(node: Any, type_name: str) -> dict | None:
    if isinstance(node, dict):
        if node.get("type") == type_name:
            return node
        for value in node.values():
            if found := _find(value, type_name):
                return found
    elif isinstance(node, list):
        for item in node:
            if found := _find(item, type_name):
                return found
    return None


def _browser_form_data(form: dict) -> dict[str, str]:
    """FormData a browser posts for a FastUI ModelForm left untouched.

    - <input>: its value, with CR/LF stripped (HTML value sanitisation).
    - <textarea>: its text, newlines normalised to CRLF on submit.
    - checkbox: "on" if checked, otherwise absent.
    """
    data: dict[str, str] = {}
    for field in form["formFields"]:
        name, initial = field["name"], field.get("initial")
        if field["type"] == "FormFieldBoolean":
            if initial:
                data[name] = "on"
        elif field["type"] == "FormFieldTextarea":
            data[name] = str(initial or "").replace("\r\n", "\n").replace("\n", "\r\n")
        elif isinstance(initial, float) and initial.is_integer():
            data[name] = str(int(initial))  # JS renders 1.0 as "1"
        else:
            data[name] = "" if initial is None else str(initial).replace("\r", "").replace("\n", "")
    return data


def _submitted_job(ui_state) -> Any:
    ui_state["queue"].submit.assert_awaited_once()
    return ui_state["queue"].submit.await_args.args[0]


class TestBatchPreviewToPrint:
    def test_list_survives_preview_then_print(self, client, ui_state):
        resp = client.post(
            "/api/print/batch/preview?printer=p1",
            data={"signals": "GND\nVCC\nSDA", "bold": "on", "quantity": "1"},
        )
        assert resp.status_code == 200
        form = _find(resp.json(), "ModelForm")
        assert form is not None

        signals = next(f for f in form["formFields"] if f["name"] == "signals")
        assert signals["type"] == "FormFieldTextarea"

        resp = client.post(form["submitUrl"], data=_browser_form_data(form))
        assert resp.status_code == 200

        job = _submitted_job(ui_state)
        items = [line.strip() for line in job.data["signals"].split("\n") if line.strip()]
        assert items == ["GND", "VCC", "SDA"]

    def test_positive_control_plain_input_merges_list(self):
        """What the old hidden <input> did to the list."""
        old_field = {"type": "FormFieldInput", "name": "signals", "initial": "GND\nVCC\nSDA"}
        assert _browser_form_data({"formFields": [old_field]})["signals"] == "GNDVCCSDA"

    def test_unchecked_boolean_survives_preview_then_print(self, client, ui_state):
        """A default-true boolean switched off must stay off when printed."""
        resp = client.post(
            "/api/print/batch/preview?printer=p1",
            data={"signals": "A", "quantity": "1"},  # bold unchecked = absent
        )
        form = _find(resp.json(), "ModelForm")
        assert form is not None
        client.post(form["submitUrl"], data=_browser_form_data(form))
        assert _submitted_job(ui_state).data["bold"] is False


class TestFormParsing:
    def test_unchecked_default_true_boolean_is_false(self, client, ui_state):
        client.post("/api/print/batch/submit?printer=p1", data={"signals": "A", "quantity": "1"})
        assert _submitted_job(ui_state).data["bold"] is False

    def test_checked_boolean_is_true(self, client, ui_state):
        client.post("/api/print/batch/submit?printer=p1", data={"signals": "A", "bold": "on", "quantity": "1"})
        assert _submitted_job(ui_state).data["bold"] is True

    def test_blank_optional_number_is_dropped(self, client, ui_state):
        resp = client.post(
            "/api/print/batch/submit?printer=p1",
            data={"signals": "A", "copies_note": "", "quantity": "1"},
        )
        assert resp.status_code == 200
        assert "copies_note" not in _submitted_job(ui_state).data

    def test_blank_quantity_defaults_to_one(self, client, ui_state):
        client.post("/api/print/batch/submit?printer=p1", data={"signals": "A", "quantity": ""})
        assert _submitted_job(ui_state).quantity == 1

    @pytest.mark.parametrize("quantity", ["abc", "1.5", "0", "-3", "100000"])
    def test_bad_quantity_shows_error_page(self, client, ui_state, quantity):
        resp = client.post("/api/print/batch/submit?printer=p1", data={"signals": "A", "quantity": quantity})
        assert resp.status_code == 200
        assert "Quantity must be" in resp.text
        ui_state["queue"].submit.assert_not_awaited()

    def test_printer_not_supported_by_template_rejected(self, client, ui_state):
        resp = client.post("/api/print/batch/submit?printer=other", data={"signals": "A", "quantity": "1"})
        assert "Invalid printer selected" in resp.text
        ui_state["queue"].submit.assert_not_awaited()

    def test_submit_navigates_to_job_page(self, client, ui_state):
        """The preview flow submits from a hidden form, so a confirmation
        rendered in place is invisible; the response must navigate instead."""
        resp = client.post("/api/print/batch/submit?printer=p1", data={"signals": "A", "quantity": "1"})
        event = _find(resp.json(), "FireEvent")
        assert event is not None
        job = _submitted_job(ui_state)
        assert event["event"] == {"type": "go-to", "url": f"/jobs/{job.id}"}


class TestJobPage:
    def _job(self, **overrides):
        from labelable.models.job import JobStatus, PrintJob

        fields = {"template_name": "batch", "printer_name": "p1", "data": {"signals": "GND\nVCC", "quantity": 2}}
        fields.update(overrides)
        job = PrintJob(**fields)
        if "status" not in overrides:
            job.status = JobStatus.PENDING
        return job

    def test_shows_status_values_and_error(self, client, ui_state):
        from labelable.models.job import JobStatus

        job = self._job(status=JobStatus.FAILED, error_message="Zebra printer not ready: paused")
        ui_state["queue"].get_job.return_value = job
        text = client.get(f"/api/jobs/{job.id}").text
        assert "Failed" in text
        assert "Zebra printer not ready: paused" in text
        assert "GND, VCC" in text

    def test_pending_on_offline_printer_says_so(self, client, ui_state):
        job = self._job()
        ui_state["queue"].get_job.return_value = job
        ui._app_state["printers"]["p1"].get_cached_online_status.return_value = False
        text = client.get(f"/api/jobs/{job.id}").text
        assert "Waiting in the print queue" in text
        assert "will print when it comes back" in text

    def test_unknown_job(self, client, ui_state):
        ui_state["queue"].get_job.return_value = None
        assert "Job not found" in client.get("/api/jobs/nope").text


class TestListFieldDefault:
    def test_textarea_shows_list_default(self, client, ui_state):
        ui._app_state["templates"]["batch"] = _template(
            fields=[TemplateField(name="signals", type=FieldType.LIST, default="GND\nVCC")]
        )
        resp = client.get("/api/print/batch?printer=p1")
        form = _find(resp.json(), "ModelForm")
        assert form is not None
        signals = next(f for f in form["formFields"] if f["name"] == "signals")
        assert signals["type"] == "FormFieldTextarea"
        assert signals["initial"] == "GND\nVCC"


class TestDisplayHelpers:
    @pytest.mark.parametrize(
        ("connection", "expected"),
        [
            ({"type": "tcp", "host": "10.0.0.5"}, "TCP 10.0.0.5:9100"),
            ({"type": "serial", "device": "/dev/ttyUSB0"}, "Serial /dev/ttyUSB0"),
            ({"type": "usb"}, "USB"),
            ({"type": "ha", "device_id": "garage"}, "Home Assistant (garage)"),
            ({"type": "bridge", "serial_number": "ABC"}, "Bridge daemon (ABC)"),
            ({"type": "ptouch_bridge", "host": "esp32-ptouch"}, "P-Touch bridge esp32-ptouch"),
            ({"type": "zebra_bridge", "host": "esp32-zebra"}, "Zebra bridge esp32-zebra"),
        ],
    )
    def test_connection_summary_covers_every_transport(self, connection, expected):
        from labelable.models.printer import PrinterConfig

        printer_type = "ptouch" if connection["type"] in ("usb", "bridge", "ptouch_bridge") else "zpl"
        config = PrinterConfig.model_validate({"name": "p", "type": printer_type, "connection": connection})
        assert ui._connection_summary(config.connection) == expected

    @pytest.mark.parametrize(
        ("cached", "errors", "expected"),
        [
            (None, None, "Checking..."),
            (True, None, "Online"),
            (False, None, "Offline"),
            (False, ["head_open", "paused"], "Offline: head open, paused"),
        ],
    )
    def test_status_text(self, cached, errors, expected):
        printer = MagicMock()
        printer.get_cached_online_status.return_value = cached
        printer.errors = errors
        assert ui._status_text(printer) == expected

    def test_circle_size_uses_diameter(self):
        circle = _template(dimensions=LabelDimensions(diameter_mm=50))
        assert ui._size_text(circle) == "50 mm diameter"
        assert ui._size_text(_template()) == "9 x 50 mm"

    def test_printer_name_is_url_encoded(self, client, ui_state):
        ui._app_state["printers"] = {"Office & Shed #1": ui._app_state["printers"]["p1"]}
        ui._app_state["templates"]["batch"] = _template(supported_printers=["Office & Shed #1"])
        form = _find(client.get("/api/print/batch").json(), "ModelForm")
        assert form is not None
        assert form["submitUrl"].endswith("?printer=Office%20%26%20Shed%20%231")
