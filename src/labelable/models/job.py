"""Print job models."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Status of a print job."""

    PENDING = "pending"
    PRINTING = "printing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class PrintJob(BaseModel):
    """A print job in the queue."""

    id: UUID = Field(default_factory=uuid4)
    template_name: str
    printer_name: str
    data: dict[str, Any]
    quantity: int = 1
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    rendered_content: bytes | None = None
    error_message: str | None = None

    def is_expired(self, timeout_seconds: int) -> bool:
        """Check if the job has expired based on timeout."""
        elapsed = (datetime.now() - self.created_at).total_seconds()
        return elapsed > timeout_seconds
