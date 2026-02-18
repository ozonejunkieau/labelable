"""Tests for bridge registration and polling API endpoints."""

import pytest
from fastapi.testclient import TestClient

from labelable.api import routes as api_routes
from labelable.app import create_app
from labelable.models.printer import BridgeConnection, PrinterConfig, PrinterType
from labelable.printers.bridge import BridgePTouchPrinter


class TestBridgeRegistration:
    """Test POST /api/v1/bridge/register endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_register_creates_new_printer(self, client: TestClient):
        """Test that registration creates a new bridge printer."""
        response = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "TEST-SERIAL-001",
                "printer_name": "rpi-ptouch",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "registered"
        assert data["printer_name"] == "rpi-ptouch"

        # Verify printer exists in app state
        printers = api_routes._app_state.get("printers", {})
        assert "rpi-ptouch" in printers
        printer = printers["rpi-ptouch"]
        assert isinstance(printer, BridgePTouchPrinter)
        assert isinstance(printer.config.connection, BridgeConnection)
        assert printer.config.connection.serial_number == "TEST-SERIAL-001"

        # Cleanup
        del printers["rpi-ptouch"]

    def test_re_register_returns_existing_name(self, client: TestClient):
        """Test that re-registration with same serial returns same printer name."""
        # First registration
        response1 = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "TEST-SERIAL-002",
                "printer_name": "my-ptouch",
            },
        )
        assert response1.status_code == 200
        name = response1.json()["printer_name"]

        # Re-register with same serial
        response2 = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "TEST-SERIAL-002",
                "printer_name": "my-ptouch",
            },
        )
        assert response2.status_code == 200
        assert response2.json()["printer_name"] == name

        # Cleanup
        printers = api_routes._app_state.get("printers", {})
        del printers[name]

    def test_register_avoids_name_collision(self, client: TestClient):
        """Test that registration picks a unique name if the name is taken."""
        # Register first printer
        response1 = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "SERIAL-A",
                "printer_name": "ptouch",
            },
        )
        assert response1.status_code == 200
        name1 = response1.json()["printer_name"]

        # Register second with same name but different serial
        response2 = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "SERIAL-B",
                "printer_name": "ptouch",
            },
        )
        assert response2.status_code == 200
        name2 = response2.json()["printer_name"]

        assert name1 != name2
        assert name2.startswith("ptouch-")

        # Cleanup
        printers = api_routes._app_state.get("printers", {})
        del printers[name1]
        del printers[name2]

    def test_registered_printer_appears_in_list(self, client: TestClient):
        """Test that a registered bridge printer appears in GET /api/v1/printers."""
        client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "TEST-SERIAL-003",
                "printer_name": "bridge-test",
            },
        )

        response = client.get("/api/v1/printers")
        assert response.status_code == 200
        printer_names = [p["name"] for p in response.json()]
        assert "bridge-test" in printer_names

        # Cleanup
        printers = api_routes._app_state.get("printers", {})
        del printers["bridge-test"]

    def test_register_with_tape_width(self, client: TestClient):
        """Test registration with tape width passes through to config."""
        response = client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "TEST-SERIAL-004",
                "printer_name": "width-test",
                "tape_width_mm": 12,
            },
        )
        assert response.status_code == 200

        printers = api_routes._app_state.get("printers", {})
        printer = printers["width-test"]
        assert printer.config.connection.tape_width_mm == 12

        # Cleanup
        del printers["width-test"]

    def test_register_missing_required_fields(self, client: TestClient):
        """Test that missing required fields return 422."""
        response = client.post(
            "/api/v1/bridge/register",
            json={},
        )
        assert response.status_code == 422


class TestBridgePolling:
    """Test bridge job polling and result reporting endpoints."""

    @pytest.fixture
    def client_with_bridge(self) -> TestClient:
        """Create a test client with a registered bridge printer."""
        app = create_app()
        client = TestClient(app)

        # Register a bridge printer
        client.post(
            "/api/v1/bridge/register",
            json={
                "serial_number": "POLL-TEST-SERIAL",
                "printer_name": "poll-test",
            },
        )

        yield client

        # Cleanup
        printers = api_routes._app_state.get("printers", {})
        printers.pop("poll-test", None)

    def test_poll_returns_204_when_no_job(self, client_with_bridge: TestClient):
        """Test that polling returns 204 when no job is pending."""
        response = client_with_bridge.get("/api/v1/bridge/poll-test/job")
        assert response.status_code == 204

    def test_poll_returns_data_when_job_pending(self, client_with_bridge: TestClient):
        """Test that polling returns raw data when a job is pending."""
        # Set up pending data directly on the printer
        printers = api_routes._app_state.get("printers", {})
        printer = printers["poll-test"]
        test_data = b"\x1b@\x1bia\x01\x00\x00"
        printer._pending_data = test_data

        response = client_with_bridge.get("/api/v1/bridge/poll-test/job")
        assert response.status_code == 200
        assert response.content == test_data
        assert response.headers["content-type"] == "application/octet-stream"

        # Verify data was cleared
        assert printer.take_pending_job() is None

    def test_report_result_success(self, client_with_bridge: TestClient):
        """Test reporting a successful result."""
        response = client_with_bridge.post(
            "/api/v1/bridge/poll-test/result",
            json={"ok": True},
        )
        assert response.status_code == 200

    def test_report_result_failure(self, client_with_bridge: TestClient):
        """Test reporting a failed result."""
        response = client_with_bridge.post(
            "/api/v1/bridge/poll-test/result",
            json={"ok": False, "error": "USB write timeout"},
        )
        assert response.status_code == 200

    def test_report_status(self, client_with_bridge: TestClient):
        """Test reporting printer status."""
        response = client_with_bridge.post(
            "/api/v1/bridge/poll-test/status",
            json={
                "online": True,
                "model_info": "P-Touch (9mm Continuous Tape)",
                "tape_width_mm": 9,
            },
        )
        assert response.status_code == 200

        # Verify status was updated
        printers = api_routes._app_state.get("printers", {})
        printer = printers["poll-test"]
        assert printer._daemon_online is True
        assert printer.model_info == "P-Touch (9mm Continuous Tape)"

    def test_report_status_extended_fields(self, client_with_bridge: TestClient):
        """Test reporting printer status with extended fields."""
        response = client_with_bridge.post(
            "/api/v1/bridge/poll-test/status",
            json={
                "online": True,
                "model_info": "P-Touch (9mm Laminated Tape)",
                "tape_width_mm": 9,
                "media_kind": "Laminated Tape",
                "tape_colour": "White",
                "text_colour": "Black",
                "low_battery": False,
                "errors": [],
            },
        )
        assert response.status_code == 200

        printers = api_routes._app_state.get("printers", {})
        printer = printers["poll-test"]
        assert printer.media_kind == "Laminated Tape"
        assert printer.tape_colour == "White"
        assert printer.text_colour == "Black"
        assert printer.low_battery is False
        assert printer.errors == []

    def test_report_status_with_errors(self, client_with_bridge: TestClient):
        """Test reporting printer status with errors and low battery."""
        response = client_with_bridge.post(
            "/api/v1/bridge/poll-test/status",
            json={
                "online": False,
                "model_info": "P-Touch (9mm Laminated Tape)",
                "tape_width_mm": 9,
                "media_kind": "Laminated Tape",
                "tape_colour": "White",
                "text_colour": "Black",
                "low_battery": True,
                "errors": ["WEAK_BATTERY", "NO_MEDIA"],
            },
        )
        assert response.status_code == 200

        printers = api_routes._app_state.get("printers", {})
        printer = printers["poll-test"]
        assert printer.low_battery is True
        assert printer.errors == ["WEAK_BATTERY", "NO_MEDIA"]

    def test_poll_nonexistent_printer_returns_404(self, client_with_bridge: TestClient):
        """Test that polling a nonexistent printer returns 404."""
        response = client_with_bridge.get("/api/v1/bridge/nonexistent/job")
        assert response.status_code == 404

    def test_status_nonexistent_printer_returns_404(self, client_with_bridge: TestClient):
        """Test that status report for nonexistent printer returns 404."""
        response = client_with_bridge.post(
            "/api/v1/bridge/nonexistent/status",
            json={"online": True},
        )
        assert response.status_code == 404


class TestBridgePrinterFactory:
    """Test that the printer factory creates BridgePTouchPrinter for bridge connections."""

    def test_factory_creates_bridge_printer(self):
        from labelable.printers import BridgePTouchPrinter, create_printer

        config = PrinterConfig(
            name="factory-test",
            type=PrinterType.PTOUCH,
            connection=BridgeConnection(serial_number="XYZ"),
        )
        printer = create_printer(config)
        assert isinstance(printer, BridgePTouchPrinter)
