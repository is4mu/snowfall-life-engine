"""Experimental schema-validation facade; schema names remain pre-v1."""

from __future__ import annotations

from engine.life.schema import load_schema_document, validate_instance

__all__ = ["load_schema_document", "validate_instance"]
