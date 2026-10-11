"""Print job models."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Upper bound on copies per request, shared by every job source. Quantity is
# looped on most transports, so an unbounded value ties a printer up for hours.
MAX_QUANTITY = 100


def resolve_quantity(requested: object, fixed: int | None) -> int:
    """Number of copies to print for a request.

    A template with a fixed ``quantity`` always wins, whatever was asked for.
    Otherwise the request's value must be a whole number from 1 to
    MAX_QUANTITY; every job source applies the same rule.

    Raises:
        ValueError: If the requested quantity is out of range or not a number.
    """
    if fixed is not None:
        return fixed
    if requested is None:
        return 1
    if isinstance(requested, bool) or not isinstance(requested, int | float | str):
        raise ValueError(f"quantity must be a whole number, not {requested!r}")
    try:
        as_float = float(requested)
    except ValueError:
        raise ValueError(f"quantity must be a whole number, not {requested!r}") from None
    if not as_float.is_integer() or not 1 <= as_float <= MAX_QUANTITY:
        raise ValueError(f"quantity must be a whole number from 1 to {MAX_QUANTITY}, not {requested!r}")
    return int(as_float)


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
