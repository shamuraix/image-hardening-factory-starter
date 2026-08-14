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
    if arf.exists():
        root = ET.parse(arf).getroot()
        for element in root.iter():
            if not element.tag.endswith("rule-result"):
                continue
            result = next((child.text for child in element if child.tag.endswith("result")), None)
            identifier = element.attrib.get("idref", "unknown")
            if result == "fail":
                failed.append(identifier)
            elif result in {"error", "unknown"}:
                error.append(identifier)
    data = {
        "passed": status in {0, 2} and not failed and not error,
        "scannerExitCode": status,
        "failedRules": sorted(failed),
        "errorRules": sorted(error),
    }
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
