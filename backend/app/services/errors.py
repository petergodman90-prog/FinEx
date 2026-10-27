"""Domain errors raised by services and mapped to HTTP responses in main.py."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for service-layer errors."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details


class AuthenticationError(DomainError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "permission_denied"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class DomainValidationError(DomainError):
    status_code = 422
    code = "validation_error"
