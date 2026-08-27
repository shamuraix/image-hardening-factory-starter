from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    gate.add_argument("--fcs-status", required=True)
    gate.add_argument("--output", required=True)

    findings = commands.add_parser("normalize-findings")
    findings.add_argument("--grype", required=True)
    findings.add_argument("--trivy", required=True)
    findings.add_argument("--osv")
    findings.add_argument("--kev")
    findings.add_argument("--baseline")
    findings.add_argument("--output", required=True)
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
            args.fcs_status,
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
