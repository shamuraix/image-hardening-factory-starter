#!/usr/bin/env python3
"""Validate staged agent changes without executing the proposed code."""

import subprocess
from pathlib import PurePosixPath

import yaml


def main():
    names = (
        subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"])
        .decode()
        .split("\0")
    )
    for name in filter(None, names):
        path = PurePosixPath(name)
        if not name.startswith(("overlays/", "catalog/images/", "tests/profiles/")):
            raise SystemExit(f"agent patch contains an unapproved path: {name}")
        if any(word in name.lower() for word in ("vex", "exception")):
            raise SystemExit(f"agent patch touches protected evidence: {name}")
        entry = subprocess.check_output(["git", "ls-files", "--stage", "--", name]).decode()
        if not entry.startswith(("100644 ", "100755 ")):
            raise SystemExit(f"agent patch cannot delete files or introduce links: {name}")
        if name.startswith("catalog/images/"):
            before = yaml.safe_load(subprocess.check_output(["git", "show", f"HEAD:{name}"]))
            after = yaml.safe_load(subprocess.check_output(["git", "show", f":{name}"]))
            # Only versions, checksums (in overlays), source revision, and build
            # arguments may change. Approval, destination and base policy are frozen.
            for document in (before, after):
                document["source"].pop("revision", None)
                document["product"].pop("version", None)
                document["build"].pop("buildArgs", None)
            if before != after:
                raise SystemExit(f"agent patch changes protected catalog fields: {path}")


if __name__ == "__main__":
    main()
