from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate(instance: Any, schema: dict[str, Any], path: str = "<root>") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "oneOf" in schema:
        alternatives = [validate(instance, alternative, path) for alternative in schema["oneOf"]]
        passing = [alternative for alternative in alternatives if not alternative]
        if len(passing) != 1:
            issues.append(ValidationIssue(path, "must match exactly one oneOf alternative"))
        return issues

    expected = schema.get("type")
    if expected:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not any(_matches_type(instance, item) for item in expected_types):
            issues.append(ValidationIssue(path, f"expected type {expected_types}"))
            return issues

    if "const" in schema and instance != schema["const"]:
        issues.append(ValidationIssue(path, f"must equal {schema['const']!r}"))
    if "enum" in schema and instance not in schema["enum"]:
        issues.append(ValidationIssue(path, f"must be one of {schema['enum']!r}"))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                issues.append(ValidationIssue(path, f"missing required property {key!r}"))
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}" if path != "<root>" else key
            if key in properties:
                issues.extend(validate(value, properties[key], child_path))
            elif additional is False:
                issues.append(ValidationIssue(child_path, "additional property is not allowed"))
            elif isinstance(additional, dict):
                issues.extend(validate(value, additional, child_path))

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            issues.append(ValidationIssue(path, f"requires at least {schema['minItems']} items"))
        if schema.get("uniqueItems") and len({repr(item) for item in instance}) != len(instance):
            issues.append(ValidationIssue(path, "array items must be unique"))
        if "items" in schema:
            for index, value in enumerate(instance):
                issues.extend(validate(value, schema["items"], f"{path}[{index}]"))

    if isinstance(instance, str):
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            issues.append(ValidationIssue(path, f"does not match pattern {schema['pattern']!r}"))
        if schema.get("format") == "uri":
            parsed = urlparse(instance)
            if not parsed.scheme or not parsed.netloc:
                issues.append(ValidationIssue(path, "must be an absolute URI"))

    if (
        isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and "minimum" in schema
        and instance < schema["minimum"]
    ):
        issues.append(ValidationIssue(path, f"must be >= {schema['minimum']}"))
    return issues
