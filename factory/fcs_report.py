"""Validate native FCS output against an operator-reviewed, versioned schema."""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


def validate(report_path: Path, schema_path: Path) -> None:
    report = json.loads(report_path.read_text())
    schema = json.loads(schema_path.read_text())
    # Do not silently accept an empty/unconstrained schema as an assessment contract.
    if schema.get("type") != "object" or not schema.get("required"):
        raise ValueError("FCS schema must require assessment fields")
    if not isinstance(report, dict) or any(report.get(key) for key in ("error", "errors")):
        raise ValueError("FCS returned an error envelope")

    def check_references(value):
        if isinstance(value, dict):
            for name, child in value.items():
                if name in ("$ref", "$dynamicRef") and not str(child).startswith("#"):
                    raise ValueError("FCS schema must bundle references locally")
                check_references(child)
        elif isinstance(value, list):
            for child in value:
                check_references(child)

    check_references(schema)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)


if __name__ == "__main__":
    validate(Path(sys.argv[1]), Path(sys.argv[2]))
