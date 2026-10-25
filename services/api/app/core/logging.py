"""Logging setup lives in packages/observability/logs.py (structured JSON, request ids, redaction of
health content). Kept here so existing imports keep working."""

from packages.observability.logs import configure_logging

__all__ = ["configure_logging"]
