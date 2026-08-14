#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from factory.schema import validate


def main() -> int:
    catalog, work = Path(sys.argv[1]), Path(sys.argv[2])
    output = work / "evidence/remediation"
    output.mkdir(parents=True, exist_ok=True)
    schema = json.loads(Path("agents/schemas/remediation-plan.schema.json").read_text())
    prompt = Path("agents/prompts/remediation-summary.txt").read_text()
    evidence = {
        "catalog": catalog.read_text(),
        "gate": json.loads((work / "evidence/gate-result.json").read_text()),
        "findings": json.loads((work / "evidence/findings.json").read_text()),
        "sbom": json.loads((work / "evidence/sbom.cdx.json").read_text()),
        "repositoryCandidates": json.loads(
            Path(
                os.environ.get(
                    "AI_REPOSITORY_CANDIDATES", "/opt/security-data/repository-candidates.json"
                )
            ).read_text()
        ),
    }
    payload = {
        "model": os.environ["AI_MODEL"],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "remediation_plan", "schema": schema, "strict": True},
        },
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(evidence, separators=(",", ":"))},
        ],
    }
    request = urllib.request.Request(
        os.environ["AI_BASE_URL"].rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('AI_API_KEY', 'internal')}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.load(response)
    plan = json.loads(result["choices"][0]["message"]["content"])
    errors = validate(plan, schema)
    if errors:
        raise SystemExit(
            "agent response failed schema validation: "
            + "; ".join(error.message for error in errors)
        )
    (output / "plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
