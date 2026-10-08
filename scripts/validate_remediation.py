#!/usr/bin/env python3
"""Validate staged remediation changes without executing the proposed code.

Kept for compatibility with existing tooling; the policy now lives in
factory.agents.validate_staged_change, which the agent change broker uses for
every persona. Without --agent this applies the cve-remediation write policy.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.agents import AgentDefinition, load_agent, validate_staged_change

DEFAULT_REMEDIATION_POLICY = AgentDefinition(
    name="cve-remediation",
    description="default remediation write policy",
    tools=("Read", "Grep", "Glob", "Edit"),
    model="opus",
    max_turns=40,
    skills=(),
    body="",
    schema="agents/schemas/remediation-plan.schema.json",
    budget_usd=8.0,
    writable_paths=("overlays/", "catalog/images/", "tests/profiles/"),
    modes=("report", "propose-change"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", help="persona whose write policy applies")
    parser.add_argument("--repo-root", default=".", help="repository holding .claude/agents")
    args = parser.parse_args()
    catalog_directory = PurePosixPath(os.environ.get("FACTORY_CATALOG_DIR", "catalog/images"))
    if catalog_directory.is_absolute() or ".." in catalog_directory.parts:
        raise SystemExit("remediation catalog directory must be relative to the repository")
    if args.agent:
        agent = load_agent(args.repo_root, args.agent)
    else:
        agent = DEFAULT_REMEDIATION_POLICY
        if str(catalog_directory) != "catalog/images":
            agent = AgentDefinition(
                **{
                    **agent.__dict__,
                    "writable_paths": ("overlays/", f"{catalog_directory}/", "tests/profiles/"),
                }
            )
    errors = validate_staged_change(Path.cwd(), agent, str(catalog_directory))
    if errors:
        raise SystemExit("agent patch rejected:\n- " + "\n- ".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
