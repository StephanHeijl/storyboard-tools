from __future__ import annotations

from typing import Any


class StoryboardError(Exception):
    """Base class for expected domain failures."""

    code = "storyboard_error"
    exit_code = 1

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailure(StoryboardError):
    code = "validation_error"
    exit_code = 2


class NotFound(StoryboardError):
    code = "not_found"
    exit_code = 3


class Conflict(StoryboardError):
    code = "conflict"
    exit_code = 4


class ExternalServiceFailure(StoryboardError):
    code = "external_service_error"
    exit_code = 5


class IntegrityFailure(StoryboardError):
    code = "integrity_error"
    exit_code = 6
