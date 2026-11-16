"""Building blocks shared by every contract module."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SKIPPED_ANSWER = "[skipped]"
"""Marker stored as `answer` when the user skips a question."""

SentimentLabel = Literal["negative", "neutral", "positive"]


def _round3(value: float) -> float:
    return round(value, 3)


Score = Annotated[float, Field(ge=0.0, le=1.0), AfterValidator(_round3)]
"""A probability / relevance score in [0, 1], rounded to 3 decimals."""


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with a 'Z' suffix, e.g. 2026-11-16T10:00:00Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Contract(BaseModel):
    """Base class for all contracts: trims strings and re-validates on assignment."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
