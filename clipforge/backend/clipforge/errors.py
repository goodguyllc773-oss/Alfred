"""Error types shared by the engine, job worker and API.

Every failure a user can hit should carry a stable ``code`` and an actionable
``hint`` — the UI shows both, and the worker uses ``retryable`` to decide between
backing off and failing immediately.
"""

from __future__ import annotations


class ClipForgeError(Exception):
    code = "ERROR"
    retryable = False
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None,
                 retryable: bool | None = None, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if http_status is not None:
            self.http_status = http_status
        self.hint = hint

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class DependencyMissing(ClipForgeError):
    code = "DEPENDENCY_MISSING"
    http_status = 503


class InvalidMedia(ClipForgeError):
    code = "INVALID_MEDIA"
    http_status = 422


class SourceMissing(ClipForgeError):
    code = "SOURCE_MISSING"
    http_status = 409


class InvalidPath(ClipForgeError):
    code = "INVALID_PATH"
    http_status = 422


class NotFound(ClipForgeError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(ClipForgeError):
    code = "CONFLICT"
    http_status = 409


class ValidationFailed(ClipForgeError):
    code = "VALIDATION_FAILED"
    http_status = 422


class RenderFailed(ClipForgeError):
    code = "RENDER_FAILED"
    retryable = True


class TranscriptionFailed(ClipForgeError):
    code = "TRANSCRIPTION_FAILED"
    retryable = True


class JobCancelled(Exception):
    """Raised inside a handler when the job's cancel flag is observed."""
