"""In-memory print queue with job expiry."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable

from labelable.models.job import JobStatus, PrintJob
from labelable.printers.base import BasePrinter

logger = logging.getLogger(__name__)


class PrintQueue:
    """In-memory print queue with per-printer queues and job expiry."""

    def __init__(self, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self._queues: dict[str, asyncio.Queue[PrintJob]] = defaultdict(asyncio.Queue)
        self._jobs: dict[str, PrintJob] = {}  # job_id -> job for status tracking
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}

    async def submit(self, job: PrintJob) -> None:
        """Submit a print job to the queue.

        Args:
            job: The print job to queue.
        """
        self._jobs[str(job.id)] = job
        await self._queues[job.printer_name].put(job)
        logger.info(f"Job {job.id} queued for printer {job.printer_name}")

    def get_job(self, job_id: str) -> PrintJob | None:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    def get_queue_size(self, printer_name: str) -> int:
        """Get the number of jobs in a printer's queue."""
        return self._queues[printer_name].qsize()

    async def start_worker(
        self,
        printer: BasePrinter,
        on_status_change: Callable[[PrintJob], None] | None = None,
    ) -> None:
        """Start a background worker for a printer.

        Args:
            printer: The printer instance to process jobs for.
            on_status_change: Optional callback when job status changes.
        """
        if printer.name in self._tasks:
            logger.warning(f"Worker for {printer.name} already running")
            return

        task = asyncio.create_task(
            self._worker_loop(printer, on_status_change),
            name=f"printer-worker-{printer.name}",
        )
        self._tasks[printer.name] = task
        logger.info(f"Started worker for printer {printer.name}")

    async def stop_worker(self, printer_name: str) -> None:
        """Stop a printer's background worker."""
        if printer_name in self._tasks:
            self._tasks[printer_name].cancel()
            try:
                await self._tasks[printer_name]
            except asyncio.CancelledError:
                pass
            del self._tasks[printer_name]
            logger.info(f"Stopped worker for printer {printer_name}")

    async def stop_all(self) -> None:
        """Stop all printer workers."""
        for printer_name in list(self._tasks.keys()):
            await self.stop_worker(printer_name)

    async def _worker_loop(
        self,
        printer: BasePrinter,
        on_status_change: Callable[[PrintJob], None] | None,
    ) -> None:
        """Background worker loop for processing print jobs."""
        queue = self._queues[printer.name]
        healthcheck_interval = printer.config.healthcheck.interval

        # Perform initial status check immediately on startup
        try:
            await printer.is_online()
            logger.debug(f"Initial status check for {printer.name} complete")
        except Exception as e:
            logger.debug(f"Initial status check for {printer.name} failed: {e}")

        while True:
            try:
                # Wait for a job with timeout for periodic status check
                try:
                    job = await asyncio.wait_for(queue.get(), timeout=float(healthcheck_interval))
                except TimeoutError:
                    # Periodically check printer status to keep cache fresh
                    try:
                        await printer.is_online()
                    except Exception:
                        pass
                    continue

                # Check if job has expired
                if job.is_expired(self.timeout_seconds):
                    job.status = JobStatus.EXPIRED
                    logger.info(f"Job {job.id} expired")
                    if on_status_change:
                        on_status_change(job)
                    continue

                # Try to print
                job.status = JobStatus.PRINTING
                if on_status_change:
                    on_status_change(job)

                try:
                    # Check printer is online
                    if not await printer.is_online():
                        # Re-queue the job and wait before retrying
                        job.status = JobStatus.PENDING
                        await queue.put(job)
                        logger.debug(f"Printer {printer.name} offline, job {job.id} re-queued")
                        await asyncio.sleep(5.0)  # Wait before checking again
                        continue

                    # Ensure connection
                    if not printer.is_connected:
                        await printer.connect()

                    # Print the job with quantity handling
                    # Printer subclass decides whether to loop or use native command
                    if job.rendered_content:
                        await printer.print_with_quantity(job.rendered_content, job.quantity)

                    job.status = JobStatus.COMPLETED
                    logger.info(f"Job {job.id} completed")

                except Exception as e:
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)
                    logger.error(f"Job {job.id} failed: {e}")

                if on_status_change:
                    on_status_change(job)

            except asyncio.CancelledError:
                logger.info(f"Worker for {printer.name} cancelled")
                raise
            except Exception as e:
                logger.error(f"Worker error for {printer.name}: {e}")
                await asyncio.sleep(1.0)  # Prevent tight error loop
