#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    arf, output, status = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    failed: list[str] = []
    error: list[str] = []
    evaluated = 0
    if arf.exists():
        root = ET.parse(arf).getroot()
        for element in root.iter():
            if not element.tag.endswith("rule-result"):
                continue
            result = next((child.text for child in element if child.tag.endswith("result")), None)
            identifier = element.attrib.get("idref", "unknown")
            if result == "pass":
                evaluated += 1
            if result == "fail":
                evaluated += 1
                failed.append(identifier)
            elif result not in {"pass", "fail", "notapplicable", "notselected"}:
                error.append(identifier)
    data = {
        "passed": status == 0 and evaluated > 0 and not failed and not error,
        "evaluatedRules": evaluated,
        "scannerExitCode": status,
        "failedRules": sorted(failed),
        "errorRules": sorted(error),
    }
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
