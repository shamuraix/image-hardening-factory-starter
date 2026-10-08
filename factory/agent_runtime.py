"""Glue between the factory-claude-agent Tekton task and factory.agents.

Layout under FACTORY_AGENT_ROOT (the agent directory on the run workspace):

    context/            read-only inputs gathered before the agent starts
    repo/               private clone of the pinned commit (agent working dir)
    system-prompt.md    composed persona prompt
    result.json         raw `claude --output-format json` result
    report.json         validated structured output
    report.md           rendered report for change requests and comments
    change.patch        staged diff when a proposal passed validation
    outcome.json        {"outcome": ..., "note": ...}
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from factory.agents import (
    CI_SETTINGS,
    build_command,
    compose_system_prompt,
    load_agent,
    load_schema,
    parse_result,
    render_report_markdown,
    validate_staged_change,
)

DEFAULT_PROMPTS = {
    "report": "Run your procedure for image `{image}` using the inputs in `{context}`.",
    "propose-change": (
        "Run your procedure for `{image}` using the inputs in `{context}`. Make the smallest "
        "change that resolves the problem, only within your writable paths, then return the "
        "structured report."
    ),
    "release-request": (
        "Assess release readiness of the `{image}` candidate described in `{context}` for the "
        "`{environment}` environment, and return the structured report."
    ),
}


def _run_context(root: Path) -> dict[str, Any]:
    context = {
        "agent": os.environ.get("FACTORY_AGENT", ""),
        "image": os.environ.get("FACTORY_IMAGE", ""),
        "mode": os.environ.get("FACTORY_AGENT_MODE", "report"),
        "eventType": os.environ.get("FACTORY_EVENT_TYPE", ""),
        "environment": os.environ.get("FACTORY_RELEASE_ENV", "commercial"),
        "commit": os.environ.get("FACTORY_COMMIT_SHA", ""),
        "branch": os.environ.get("FACTORY_BRANCH_NAME", ""),
        "changeId": os.environ.get("FACTORY_CHANGE_ID", ""),
        "pipelineRun": os.environ.get("FACTORY_PIPELINERUN", ""),
        "contextDir": str(root / "context"),
    }
    run_file = root / "context/run.json"
    if run_file.exists():
        context["taskStatuses"] = json.loads(run_file.read_text(encoding="utf-8")).get(
            "taskStatuses", {}
        )
    return context


def prepare_command(agent_name: str, root: Path, source: Path, claude: str = "claude") -> list[str]:
    agent = load_agent(source, agent_name)
    context = _run_context(root)
    mode = context["mode"]
    if mode not in agent.modes:
        raise SystemExit(f"agent {agent_name} does not support mode {mode}")
    prompt_file = root / "system-prompt.md"
    prompt_file.write_text(compose_system_prompt(source, agent, context), encoding="utf-8")
    template = str(agent.extra.get("prompt") or DEFAULT_PROMPTS[mode])
    user_prompt = template.format(
        image=context["image"], context=context["contextDir"], environment=context["environment"]
    )
    model = os.environ.get(f"FACTORY_AGENT_MODEL_{agent_name.upper().replace('-', '_')}") or (
        os.environ.get("FACTORY_AGENT_MODEL") or None
    )
    return build_command(
        agent,
        user_prompt=user_prompt,
        system_prompt_file=str(prompt_file),
        settings_file=str(source / CI_SETTINGS),
        schema=load_schema(source, agent),
        context_dir=context["contextDir"],
        model=model,
        claude=claude,
    )


def _write_outcome(root: Path, outcome: str, note: str, outcome_file: Path | None) -> None:
    (root / "outcome.json").write_text(
        json.dumps({"outcome": outcome, "note": note}, indent=2) + "\n", encoding="utf-8"
    )
    if outcome_file:
        outcome_file.write_text(outcome, encoding="utf-8")


def validate_run(
    agent_name: str,
    root: Path,
    source: Path,
    exit_code: int,
    outcome_file: Path | None = None,
    report_sha256_file: Path | None = None,
) -> str:
    agent = load_agent(source, agent_name)
    mode = os.environ.get("FACTORY_AGENT_MODE", "report")
    report, note = parse_result(root / "result.json", load_schema(source, agent))
    if exit_code != 0 and report is None:
        note = note or f"agent exited {exit_code}"
    repo = root / "repo"
    outcome = "report" if report is not None else "agent-failed"
    patch = root / "change.patch"
    patch.unlink(missing_ok=True)
    if repo.exists():
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        diff = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=repo)
        if diff and mode != "propose-change":
            note = (
                note + " " if note else ""
            ) + "Repository edits were discarded (report-only run)."
        elif diff and report is not None:
            errors = validate_staged_change(repo, agent)
            if errors:
                outcome = "change-rejected"
                note = "Proposal rejected by policy: " + "; ".join(errors[:10])
            else:
                patch.write_bytes(diff)
                outcome = "change-proposed"
        elif mode == "propose-change" and report is not None:
            outcome = "no-op"
    if report is not None:
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_report_markdown(agent_name, report, outcome, note)
    (root / "report.md").write_text(markdown, encoding="utf-8")
    _write_outcome(root, outcome, note, outcome_file)
    if report_sha256_file:
        report_sha256_file.write_text(
            hashlib.sha256(markdown.encode()).hexdigest(), encoding="utf-8"
        )
    print(f"agent {agent_name}: {outcome}" + (f" ({note})" if note else ""))
    return outcome
