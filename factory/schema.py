from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def validate(instance: Any, schema: dict[str, Any], path: str = "<root>") -> list[ValidationIssue]:
    """Validate with the declared JSON Schema dialect without network resolution."""
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        ValidationIssue(".".join(map(str, error.absolute_path)) or path, error.message)
        for error in validator.iter_errors(instance)
    ]
