from __future__ import annotations

import argparse
import json
from pathlib import Path

from factory import agent_runtime, release, tekton
from factory.agents import list_agents, load_agent, validate_staged_change
from factory.catalog import load_catalog, load_image
from factory.findings import normalize
from factory.gate import gate_input, write_gate_input
from factory.intake import resolve_manifest, upload_locked_files
from factory.pipeline import render_plan, write_plan


def _catalog_path(value: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--catalog", type=_catalog_path, required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--catalog", type=_catalog_path, required=True)
    selection = plan.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--changed", nargs="+")
    plan.add_argument("--output", required=True)

    intake = commands.add_parser("intake")
    intake.add_argument("--image", type=_catalog_path, required=True)
    intake.add_argument("--source-dir", type=_catalog_path, required=True)
    intake.add_argument("--cache-dir", required=True)
    intake.add_argument("--output", required=True)
    intake.add_argument("--upload-url")

    gate = commands.add_parser("gate-input")
    gate.add_argument("--image", required=True)
    gate.add_argument("--image-digest", required=True)
    gate.add_argument("--sbom", required=True)
    gate.add_argument("--findings", required=True)
    gate.add_argument("--compliance", required=True)
    gate.add_argument("--tests", required=True)
    gate.add_argument("--database-status", required=True)
    gate.add_argument("--assessment-status", required=True)
    gate.add_argument("--output", required=True)

    findings = commands.add_parser("normalize-findings")
    findings.add_argument("--grype", required=True)
    findings.add_argument("--trivy", required=True)
    findings.add_argument("--osv")
    findings.add_argument("--kev")
    findings.add_argument("--baseline")
    findings.add_argument("--output", required=True)

    render = commands.add_parser("tekton-render", help="render .tekton PipelineRuns")
    render.add_argument("--catalog", type=_catalog_path, required=True)
    render.add_argument("--output", default=".tekton")
    render.add_argument("--default-branch", default="main")
    render.add_argument("--check", action="store_true", help="fail on drift instead of writing")

    commands.add_parser("agent-list")

    command = commands.add_parser("agent-command", help="print the claude argv as JSON")
    command.add_argument("--agent", required=True)
    command.add_argument("--root", type=Path, required=True)
    command.add_argument("--source", type=Path, required=True)
    command.add_argument("--claude", default="claude")

    agent_validate = commands.add_parser("agent-validate")
    agent_validate.add_argument("--agent", required=True)
    agent_validate.add_argument("--root", type=Path, required=True)
    agent_validate.add_argument("--source", type=Path, default=Path("."))
    agent_validate.add_argument("--exit-code", type=int, required=True)
    agent_validate.add_argument("--outcome-file", type=Path)
    agent_validate.add_argument("--report-sha256-file", type=Path)

    agent_check = commands.add_parser("agent-check", help="validate staged changes in cwd")
    agent_check.add_argument("--agent", required=True)
    agent_check.add_argument("--repo-root", type=Path, default=Path("."))

    request = commands.add_parser("release-request")
    request_commands = request.add_subparsers(dest="request_command", required=True)
    request_resolve = request_commands.add_parser("resolve")
    request_resolve.add_argument("--catalog", default="catalog/images")
    request_resolve.add_argument("--commit", required=True)
    request_resolve.add_argument("--output", required=True)
    request_validate = request_commands.add_parser("validate")
    request_validate.add_argument("--catalog", default="catalog/images")
    request_validate.add_argument("files", nargs="*")
    request_write = request_commands.add_parser("write")
    request_write.add_argument("--work-dir", type=Path, required=True)
    request_write.add_argument("--catalog-file", type=Path, required=True)
    request_write.add_argument("--environment", required=True)
    request_write.add_argument("--pipeline-run", default="")
    request_write.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "validate":
        images = load_catalog(args.catalog)
        print(json.dumps({"valid": True, "images": sorted(images)}, indent=2))
        return 0
    if args.command == "plan":
        images = load_catalog(args.catalog)
        roots = None if args.all else set(args.changed)
        write_plan(render_plan(images, roots), args.output)
        return 0
    if args.command == "intake":
        image = load_image(args.image)
        lock = resolve_manifest(
            source_dir=args.source_dir,
            manifest_name=image.data["source"]["hardeningManifest"],
            source_revision=image.data["source"]["revision"],
            output=args.output,
            cache_dir=args.cache_dir,
        )
        if args.upload_url:
            upload_locked_files(lock, args.cache_dir, args.upload_url)
        return 0
    if args.command == "gate-input":
        data = gate_input(
            args.image,
            args.image_digest,
            args.sbom,
            args.findings,
            args.compliance,
            args.tests,
            args.database_status,
            args.assessment_status,
        )
        write_gate_input(data, args.output)
        return 0
    if args.command == "normalize-findings":
        data = normalize(
            Path(args.grype),
            Path(args.trivy),
            Path(args.osv) if args.osv else None,
            Path(args.kev) if args.kev else None,
            Path(args.baseline) if args.baseline else None,
        )
        Path(args.output).write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.command == "tekton-render":
        rendered = tekton.render(args.catalog, tekton.RenderOptions(args.default_branch))
        if args.check:
            problems = tekton.drift(rendered, args.output)
            if problems:
                print("\n".join(problems))
                print("run `make tekton-render` and commit the result")
                return 1
            return 0
        for path in tekton.write(rendered, args.output):
            print(path)
        return 0
    if args.command == "agent-list":
        for name in list_agents("."):
            agent = load_agent(".", name)
            print(f"{name}\t{','.join(agent.modes)}\t{agent.description}")
        return 0
    if args.command == "agent-command":
        argv = agent_runtime.prepare_command(args.agent, args.root, args.source, args.claude)
        print(json.dumps({"argv": argv}))
        return 0
    if args.command == "agent-validate":
        agent_runtime.validate_run(
            args.agent,
            args.root,
            args.source,
            args.exit_code,
            args.outcome_file,
            args.report_sha256_file,
        )
        return 0
    if args.command == "agent-check":
        errors = validate_staged_change(Path.cwd(), load_agent(args.repo_root, args.agent))
        if errors:
            print("agent change rejected:\n- " + "\n- ".join(errors))
            return 1
        return 0
    if args.command == "release-request":
        return _release_request(args)
    return 2


def _release_request(args: argparse.Namespace) -> int:
    if args.request_command == "resolve":
        data = release.resolve(".", args.catalog, args.commit)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return 0
    if args.request_command == "validate":
        files = args.files or sorted(str(path) for path in Path("releases").glob("*/*.yaml"))
        for name in files:
            release.load_request(name, args.catalog)
        print(json.dumps({"valid": True, "requests": files}, indent=2))
        return 0
    data = release.build_request(
        args.work_dir, args.catalog_file, args.environment, args.pipeline_run
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(release.dump_request(data), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
