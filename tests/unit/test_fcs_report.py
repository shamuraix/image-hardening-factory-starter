import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import ValidationError

from factory.fcs_report import validate


class FcsReportTests(unittest.TestCase):
    def test_schema_validation_rejects_error_envelopes_and_empty_contracts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report, schema = root / "report.json", root / "schema.json"
            # This is a validator test fixture, not a claim about FCS's native schema.
            contract = {
                "type": "object",
                "required": ["fixtureAssessment"],
                "properties": {"fixtureAssessment": {"type": "object"}},
            }
            schema.write_text(json.dumps(contract))
            report.write_text('{"fixtureAssessment":{}}')
            validate(report, schema)
            for document in ({"error": "unavailable"}, {}, {"fixtureAssessment": None}):
                report.write_text(json.dumps(document))
                with self.assertRaises((ValueError, ValidationError)):
                    validate(report, schema)
            report.write_text('{"fixtureAssessment":{}}')
            for invalid_contract in (
                {},
                {"type": "object"},
                {**contract, "$ref": "https://invalid/schema"},
            ):
                schema.write_text(json.dumps(invalid_contract))
                with self.assertRaises(ValueError):
                    validate(report, schema)
