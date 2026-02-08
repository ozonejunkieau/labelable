"""Tests for print queue."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from labelable.models.job import JobStatus, PrintJob
from labelable.models.printer import HealthcheckConfig, PrinterConfig, TCPConnection
from labelable.printers.base import BasePrinter
from labelable.queue import PrintQueue


class MockPrinter(BasePrinter):
    """Mock printer for testing."""

    def __init__(self, name: str = "test-printer", online: bool = True):
        config = PrinterConfig(
            name=name,
            type="zpl",
            connection=TCPConnection(host="127.0.0.1", port=9100),
            healthcheck=HealthcheckConfig(interval=60, command="~HS"),
        )
        super().__init__(config)
        self._online = online
        self._printed_data: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_online(self) -> bool:
        self._update_cache(self._online)
        return self._online

    async def get_media_size(self) -> tuple[float, float] | None:
        return None

    async def print_raw(self, data: bytes) -> None:
        self._printed_data.append(data)


class TestPrintQueue:
    """Tests for PrintQueue class."""

    @pytest.fixture
    def queue(self):
        """Create a print queue with short timeout."""
        return PrintQueue(timeout_seconds=60)

    @pytest.fixture
    def mock_printer(self):
        """Create a mock printer."""
        return MockPrinter()

    @pytest.fixture
    def sample_job(self):
        """Create a sample print job."""
        return PrintJob(
            id=uuid4(),
            template_name="test-template",
            printer_name="test-printer",
            data={"title": "Test"},
            rendered_content=b"^XA^FDTest^FS^XZ",
            quantity=1,
            created_at=datetime.now(),
            status=JobStatus.PENDING,
        )

    @pytest.mark.asyncio
    async def test_submit_job(self, queue, sample_job):
        """Test submitting a job to the queue."""
        await queue.submit(sample_job)

        assert queue.get_queue_size("test-printer") == 1
        assert queue.get_job(str(sample_job.id)) == sample_job

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, queue):
        """Test getting a non-existent job."""
        assert queue.get_job("nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_get_queue_size_empty(self, queue):
        """Test queue size for printer with no jobs."""
        assert queue.get_queue_size("nonexistent-printer") == 0

    @pytest.mark.asyncio
    async def test_start_worker(self, queue, mock_printer):
        """Test starting a worker for a printer."""
        await queue.start_worker(mock_printer)

        assert mock_printer.name in queue._tasks
        assert not queue._tasks[mock_printer.name].done()

        # Clean up
        await queue.stop_worker(mock_printer.name)

    @pytest.mark.asyncio
    async def test_start_worker_already_running(self, queue, mock_printer):
        """Test starting a worker when one is already running."""
        await queue.start_worker(mock_printer)
        await queue.start_worker(mock_printer)  # Should log warning but not crash

        assert len([t for t in queue._tasks.values() if not t.done()]) == 1

        await queue.stop_worker(mock_printer.name)

    @pytest.mark.asyncio
    async def test_stop_worker(self, queue, mock_printer):
        """Test stopping a worker."""
        await queue.start_worker(mock_printer)
        await queue.stop_worker(mock_printer.name)

        assert mock_printer.name not in queue._tasks

    @pytest.mark.asyncio
    async def test_stop_worker_not_running(self, queue):
        """Test stopping a worker that isn't running."""
        # Should not raise
        await queue.stop_worker("nonexistent-printer")

    @pytest.mark.asyncio
    async def test_stop_all(self, queue):
        """Test stopping all workers."""
        printer1 = MockPrinter("printer1")
        printer2 = MockPrinter("printer2")

        await queue.start_worker(printer1)
        await queue.start_worker(printer2)

        assert len(queue._tasks) == 2

        await queue.stop_all()

        assert len(queue._tasks) == 0

    @pytest.mark.asyncio
    async def test_worker_processes_job(self, queue, mock_printer, sample_job):
        """Test that worker processes a submitted job."""
        status_changes = []

        def on_status_change(job):
            status_changes.append(job.status)

        await queue.start_worker(mock_printer, on_status_change=on_status_change)
        await queue.submit(sample_job)

        # Wait for job to be processed
        await asyncio.sleep(0.1)

        assert sample_job.status == JobStatus.COMPLETED
        assert JobStatus.PRINTING in status_changes
        assert JobStatus.COMPLETED in status_changes
        assert len(mock_printer._printed_data) == 1

        await queue.stop_worker(mock_printer.name)

    @pytest.mark.asyncio
    async def test_worker_handles_expired_job(self, queue, mock_printer):
        """Test that worker handles expired jobs."""
        # Create an expired job
        expired_job = PrintJob(
            id=uuid4(),
            template_name="test-template",
            printer_name="test-printer",
            data={},
            rendered_content=b"test",
            quantity=1,
            created_at=datetime.now() - timedelta(seconds=120),  # Created 2 min ago
            status=JobStatus.PENDING,
        )

        queue_short_timeout = PrintQueue(timeout_seconds=60)

        status_changes = []

        def on_status_change(job):
            status_changes.append(job.status)

        await queue_short_timeout.start_worker(mock_printer, on_status_change=on_status_change)
        await queue_short_timeout.submit(expired_job)

        await asyncio.sleep(0.1)

        assert expired_job.status == JobStatus.EXPIRED
        assert JobStatus.EXPIRED in status_changes
        assert len(mock_printer._printed_data) == 0  # Should not print

        await queue_short_timeout.stop_worker(mock_printer.name)

    @pytest.mark.asyncio
    async def test_worker_handles_offline_printer(self, queue):
        """Test that worker re-queues job when printer is offline."""
        offline_printer = MockPrinter("offline-printer", online=False)

        job = PrintJob(
            id=uuid4(),
            template_name="test-template",
            printer_name="offline-printer",
            data={},
            rendered_content=b"test",
            quantity=1,
            created_at=datetime.now(),
            status=JobStatus.PENDING,
        )

        await queue.start_worker(offline_printer)
        await queue.submit(job)

        # Wait a bit for the job to be attempted
        await asyncio.sleep(0.2)

        # Job should still be pending (re-queued)
        assert job.status == JobStatus.PENDING

        await queue.stop_worker(offline_printer.name)

    @pytest.mark.asyncio
    async def test_worker_handles_print_error(self, queue, sample_job):
        """Test that worker handles print errors."""
        error_printer = MockPrinter()

        async def failing_print(data: bytes) -> None:
            raise Exception("Print failed")

        error_printer.print_raw = failing_print

        status_changes = []

        def on_status_change(job):
            status_changes.append(job.status)

        await queue.start_worker(error_printer, on_status_change=on_status_change)
        await queue.submit(sample_job)

        await asyncio.sleep(0.1)

        assert sample_job.status == JobStatus.FAILED
        assert "Print failed" in sample_job.error_message
        assert JobStatus.FAILED in status_changes

        await queue.stop_worker(error_printer.name)

    @pytest.mark.asyncio
    async def test_worker_connects_if_needed(self, queue, mock_printer, sample_job):
        """Test that worker connects to printer if not connected."""
        assert not mock_printer.is_connected

        await queue.start_worker(mock_printer)
        await queue.submit(sample_job)

        await asyncio.sleep(0.1)

        assert mock_printer.is_connected
        assert sample_job.status == JobStatus.COMPLETED

        await queue.stop_worker(mock_printer.name)

    @pytest.mark.asyncio
    async def test_multiple_jobs_processed_in_order(self, queue, mock_printer):
        """Test that multiple jobs are processed in order."""
        jobs = []
        for i in range(3):
            job = PrintJob(
                id=uuid4(),
                template_name="test-template",
                printer_name="test-printer",
                data={"index": i},
                rendered_content=f"job-{i}".encode(),
                quantity=1,
                created_at=datetime.now(),
                status=JobStatus.PENDING,
            )
            jobs.append(job)

        await queue.start_worker(mock_printer)

        for job in jobs:
            await queue.submit(job)

        # Wait for all jobs to complete
        await asyncio.sleep(0.3)

        for job in jobs:
            assert job.status == JobStatus.COMPLETED

        assert len(mock_printer._printed_data) == 3
        assert mock_printer._printed_data == [b"job-0", b"job-1", b"job-2"]

        await queue.stop_worker(mock_printer.name)
