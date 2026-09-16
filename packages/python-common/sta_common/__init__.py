"""Shared runtime primitives for Smart Travel Assistant Python services.

Only cross-cutting concerns live here (observability, error envelope, HTTP
resilience, health, app factory). Domain models belong to ``sta_contracts``;
business logic belongs to each service.
"""

from sta_common.errors import AppError, ErrorCode

__all__ = ["AppError", "ErrorCode"]
