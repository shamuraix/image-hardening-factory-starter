#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    work = Path(sys.argv[1])
    output = Path(sys.argv[2])
    build = load(work / "image-metadata.json")
    lock = load(work / "resource-lock.json")
    predicate = {
        "buildDefinition": {
            "buildType": "https://factory.platform.example/buildah/v1",
            "externalParameters": {
                "sourceRevision": build["sourceRevision"],
                "factoryRevision": build["factoryRevision"],
                "baseRef": build["baseRef"],
                "baseDigest": build["baseDigest"],
                "rpmSnapshot": build["rpmSnapshot"],
                "rpmRepomdDigest": build["rpmRepomdDigest"],
            },
            "resolvedDependencies": [
                {
                    "uri": item["source"],
                    "digest": {
                        item["declaredDigest"].split(":", 1)[0]: item["declaredDigest"].split(
                            ":", 1
                        )[1]
                    },
                }
                for item in lock["resources"]
            ],
        },
        "runDetails": {
            "builder": {"id": os.environ.get("CI_RUNNER_ID", "local")},
            "metadata": {
                "invocationId": os.environ.get("CI_PIPELINE_URL", "local"),
                "startedOn": build["created"],
            },
        },
    }
    output.write_text(json.dumps(predicate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
