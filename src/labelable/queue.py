"""In-memory print queue with job expiry."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from labelable.models.job import JobStatus, PrintJob
from labelable.printers.base import BasePrinter

logger = logging.getLogger(__name__)

# Finished jobs stay queryable (GET /api/v1/jobs/{id}, the UI job page) for
# this long, and at most this many are kept. Without a limit every job,
# including its rendered bytes, was kept for the life of the process.
JOB_HISTORY_SECONDS = 3600
MAX_JOB_HISTORY = 1000

# How often a worker re-checks an offline printer while holding a job
OFFLINE_RETRY_SECONDS = 5.0

_FINISHED = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.EXPIRED)


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
        self._prune_history()
        self._jobs[str(job.id)] = job
        await self._queues[job.printer_name].put(job)
        logger.info(f"Job {job.id} queued for printer {job.printer_name}")

    def _prune_history(self) -> None:
        """Forget finished jobs past the retention window or over the cap."""
        now = datetime.now()
        finished = [(job_id, job) for job_id, job in self._jobs.items() if job.status in _FINISHED]
        expired = [job_id for job_id, job in finished if (now - job.created_at).total_seconds() > JOB_HISTORY_SECONDS]
        for job_id in expired:
            del self._jobs[job_id]
        overflow = len(self._jobs) + 1 - MAX_JOB_HISTORY  # leave room for the job being added
        if overflow > 0:
            oldest = sorted((item for item in finished if item[0] in self._jobs), key=lambda item: item[1].created_at)
            for job_id, _job in oldest[:overflow]:
                del self._jobs[job_id]

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

    @staticmethod
    def _finish(job: PrintJob, status: JobStatus) -> None:
        """Mark a job finished and drop its rendered bytes, which are no
        longer needed but were kept for the life of the process."""
        job.status = status
        job.rendered_content = None

    @staticmethod
    async def _check_online(printer: BasePrinter) -> bool:
        """is_online, treating an error as offline rather than failing the job."""
        try:
            return await printer.is_online()
        except Exception as e:
            logger.warning(f"Printer {printer.name}: status check failed - {e}")
            return False

    @staticmethod
    async def _refresh_details(printer: BasePrinter) -> None:
        """refresh_details, which only feeds the UI, so a failure is never fatal."""
        try:
            await printer.refresh_details()
        except Exception as e:
            logger.debug(f"Printer {printer.name}: details refresh failed - {e}")

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
        await self._refresh_details(printer)

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
                    await self._refresh_details(printer)
                    continue

                # Check if job has expired
                if job.is_expired(self.timeout_seconds):
                    self._finish(job, JobStatus.EXPIRED)
                    logger.info(f"Job {job.id} expired")
                    if on_status_change:
                        on_status_change(job)
                    continue

                # Wait for the printer, holding this job at the head of the line.
                # Re-queueing it at the back instead let later jobs overtake it.
                # The cache is used when fresh to avoid a healthcheck on the same
                # socket right before print data (risks interleaving).
                if not printer.get_cached_online_status():
                    expired = False
                    while not await self._check_online(printer):
                        if job.is_expired(self.timeout_seconds):
                            expired = True
                            break
                        logger.debug(f"Printer {printer.name} offline, holding job {job.id}")
                        await asyncio.sleep(OFFLINE_RETRY_SECONDS)
                    if expired:
                        self._finish(job, JobStatus.EXPIRED)
                        logger.info(f"Job {job.id} expired waiting for {printer.name}")
                        if on_status_change:
                            on_status_change(job)
                        continue

                # Try to print
                job.status = JobStatus.PRINTING
                if on_status_change:
                    on_status_change(job)

                try:
                    # Ensure connection
                    if not printer.is_connected:
                        await printer.connect()

                    # Print the job with quantity handling
                    # Printer subclass decides whether to loop or use native command
                    # Set _printing flag to suppress healthchecks during print
                    printer._printing = True
                    try:
                        if job.rendered_content:
                            await printer.print_with_quantity(job.rendered_content, job.quantity)
                    finally:
                        printer._printing = False

                    self._finish(job, JobStatus.COMPLETED)
                    logger.info(f"Job {job.id} completed")

                except Exception as e:
                    job.error_message = str(e)
                    self._finish(job, JobStatus.FAILED)
                    logger.error(f"Job {job.id} failed: {e}")

                if on_status_change:
                    on_status_change(job)

            except asyncio.CancelledError:
                logger.info(f"Worker for {printer.name} cancelled")
                raise
            except Exception as e:
                logger.error(f"Worker error for {printer.name}: {e}")
                await asyncio.sleep(1.0)  # Prevent tight error loop
