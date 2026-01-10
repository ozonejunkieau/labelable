"""Tests for Pydantic models."""

import pytest

from labelable.models.job import JobStatus, PrintJob
from labelable.models.printer import PrinterConfig, PrinterType, TCPConnection
from labelable.models.template import (
    FieldType,
    LabelDimensions,
    TemplateConfig,
    TemplateField,
)


class TestPrinterConfig:
    def test_tcp_printer_config(self):
        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100", port=9100),
        )
        assert config.name == "test-zpl"
        assert config.type == PrinterType.ZPL
        assert config.connection.host == "192.168.1.100"
        assert config.connection.port == 9100
        assert config.enabled is True


class TestTemplateConfig:
    @pytest.fixture
    def sample_template(self) -> TemplateConfig:
        return TemplateConfig(
            name="test-label",
            description="A test label",
            dimensions=LabelDimensions(width_mm=100, height_mm=50),
            supported_printers=[PrinterType.ZPL],
            fields=[
                TemplateField(name="title", type=FieldType.STRING, required=True),
                TemplateField(name="count", type=FieldType.INTEGER, required=False, default=1),
            ],
            template="^XA^FD{{ title }}^FS^XZ",
        )

    def test_template_creation(self, sample_template: TemplateConfig):
        assert sample_template.name == "test-label"
        assert sample_template.dimensions.width_mm == 100
        assert len(sample_template.fields) == 2

    def test_get_field(self, sample_template: TemplateConfig):
        field = sample_template.get_field("title")
        assert field is not None
        assert field.name == "title"
        assert field.required is True

        missing = sample_template.get_field("nonexistent")
        assert missing is None

    def test_validate_data_with_required_fields(self, sample_template: TemplateConfig):
        data = {"title": "Test Title"}
        validated = sample_template.validate_data(data)
        assert validated["title"] == "Test Title"
        assert validated["count"] == 1  # Default applied

    def test_validate_data_missing_required(self, sample_template: TemplateConfig):
        with pytest.raises(ValueError, match="Missing required field"):
            sample_template.validate_data({})

    def test_validate_data_type_coercion(self, sample_template: TemplateConfig):
        data = {"title": "Test", "count": "5"}
        validated = sample_template.validate_data(data)
        assert validated["count"] == 5
        assert isinstance(validated["count"], int)

    def test_validate_data_boolean_from_html_checkbox(self):
        """Test that 'on' value from HTML checkboxes is correctly parsed as True."""
        template = TemplateConfig(
            name="bool-test",
            dimensions=LabelDimensions(width_mm=50, height_mm=25),
            supported_printers=[PrinterType.ZPL],
            fields=[
                TemplateField(
                    name="enabled",
                    type=FieldType.BOOLEAN,
                    required=True,
                    default=False,
                ),
            ],
            template="^XA^XZ",
        )

        # HTML checkbox sends "on" when checked
        validated = template.validate_data({"enabled": "on"})
        assert validated["enabled"] is True

        # Various truthy string values
        assert template.validate_data({"enabled": "true"})["enabled"] is True
        assert template.validate_data({"enabled": "1"})["enabled"] is True
        assert template.validate_data({"enabled": "yes"})["enabled"] is True

        # Falsy string values
        assert template.validate_data({"enabled": "false"})["enabled"] is False
        assert template.validate_data({"enabled": "0"})["enabled"] is False

        # Missing field uses default
        assert template.validate_data({})["enabled"] is False


class TestPrintJob:
    def test_job_creation(self):
        job = PrintJob(
            template_name="test",
            printer_name="printer1",
            data={"title": "Hello"},
        )
        assert job.template_name == "test"
        assert job.status == JobStatus.PENDING
        assert job.quantity == 1
        assert job.id is not None

    def test_job_expiry(self):
        job = PrintJob(
            template_name="test",
            printer_name="printer1",
            data={},
        )
        # Job just created should not be expired
        assert job.is_expired(300) is False

        # With 0 timeout, should be expired immediately
        assert job.is_expired(0) is True
