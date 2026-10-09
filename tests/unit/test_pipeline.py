import unittest
from pathlib import Path

from factory.catalog import load_catalog
from factory.pipeline import render_plan
from tests.unit.tekton_support import (
    param,
    pipeline,
    pipeline_task,
    seal_inputs,
    secret_names,
    step,
)

ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.images = load_catalog(ROOT / "catalog/images")

    def test_ubi9_plan_contains_parallel_application_wave(self) -> None:
        plan = render_plan(self.images, {"ubi9-minimal"})
        self.assertEqual(plan["waves"][0], ["ubi9-minimal"])
        self.assertEqual(
            plan["waves"][1],
            ["bitbucket-lts", "confluence-lts", "jira-lts"],
        )
        self.assertNotIn("ubi10-minimal", {image["name"] for image in plan["images"]})

    def test_application_records_selected_base_dependency(self) -> None:
        plan = render_plan(self.images, {"ubi9-minimal"})
        jira = next(image for image in plan["images"] if image["name"] == "jira-lts")
        self.assertEqual(jira["dependsOn"], ["ubi9-minimal"])

    def test_single_application_does_not_force_unchanged_base_build(self) -> None:
        plan = render_plan(self.images, {"jira-lts"})
        self.assertEqual([image["name"] for image in plan["images"]], ["jira-lts"])
        self.assertEqual(plan["images"][0]["dependsOn"], [])

    def test_plan_rejects_unknown_images(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown changed images"):
            render_plan(self.images, {"unknown-image"})

    def test_gate_requires_build_evidence_and_selects_assessment(self) -> None:
        self.assertEqual(
            seal_inputs("factory-image-build", "gate"),
            {"build", "sbom", "scan", "assessment", "compliance", "test"},
        )
        self.assertEqual(seal_inputs("factory-image-build", "assessment"), {"scan"})

    def test_retired_placeholder_stages_are_gone(self) -> None:
        names = {entry["name"] for entry in pipeline("factory-image-build")["spec"]["tasks"]}
        self.assertFalse({"helmper", "copacetic", "hummingbird"} & names)

    def test_scan_stage_emits_delegated_scanner_assessment(self) -> None:
        script = (ROOT / "scripts/scan_image.sh").read_text(encoding="utf-8")
        self.assertIn("trivy-grype-syft-osv-scanner", script)
        self.assertIn('"${scans}/delegated/status.json"', script)
        self.assertIn("blocked_count=", script)
        self.assertIn("assessmentPassed:$assessmentPassed", script)
        self.assertIn(".policy.block.critical", script)

    def test_quarantine_import_preserves_the_scanned_digest(self) -> None:
        script = (ROOT / "scripts/import_image.sh").read_text(encoding="utf-8")
        self.assertIn("skopeo copy --preserve-digests", script)
        self.assertIn('[[ "${digest}" == "${candidate_digest}" ]]', script)

    def test_delegated_scanners_feed_policy_and_gate(self) -> None:
        # Scan is non-blocking so evidence always reaches assessment and triage;
        # the gate itself is blocking and fails the PipelineRun on deny.
        self.assertEqual(
            param(pipeline_task("factory-image-build", "scan"), "allow-failure"), "true"
        )
        gate = pipeline_task("factory-image-build", "gate")
        self.assertNotIn("allow-failure", {item["name"] for item in gate["params"]})
        self.assertEqual(step("factory-stage", "run")["onError"], "continue")

        policy = (ROOT / "policies/rego/factory/release/release.rego").read_text(encoding="utf-8")
        self.assertIn("input.assessment.assessmentPassed", policy)
        self.assertIn('backend == "delegated-scanners"', policy)

        gate_script = (ROOT / "scripts/evaluate_gate.sh").read_text(encoding="utf-8")
        self.assertIn('(.findings | type == "array")', gate_script)
        self.assertIn('--assessment-status "${status}"', gate_script)

    def test_quarantine_verifies_every_signed_evidence_seal(self) -> None:
        self.assertEqual(
            seal_inputs("factory-image-build", "quarantine"),
            {"prepare", "build", "sbom", "scan", "assessment", "compliance", "test", "gate"},
        )
        guard = pipeline_task("factory-image-build", "quarantine")["when"][0]
        self.assertEqual(guard["input"], "$(params.publish)")

    def test_signing_stage_uses_scoped_environment_credentials(self) -> None:
        self.assertEqual(
            secret_names("factory-attest"),
            {"factory-cosign-$(params.environment)", "factory-artifactory-sign"},
        )
        sign = step("factory-attest", "sign")
        self.assertIn("/credentials/signing", sign["volumeMounts"][0]["mountPath"])
        verify = step("factory-attest", "verify")
        self.assertNotIn("volumeMounts", verify)
        self.assertNotIn("env", verify)

    def test_promotion_requires_resolved_evidence_and_attestation(self) -> None:
        self.assertEqual(seal_inputs("factory-image-release", "promote"), {"resolve", "attest"})
        self.assertEqual(seal_inputs("factory-image-release", "attest"), {"resolve"})

    def test_change_requests_cannot_publish_to_quarantine(self) -> None:
        script = (ROOT / "scripts/import_image.sh").read_text(encoding="utf-8")
        self.assertIn("FACTORY_PROTECTED_PUBLISH", script)
        self.assertIn("candidateOnly:true", script)

    def test_build_pipeline_has_no_signing_or_promotion(self) -> None:
        names = {
            entry["name"]
            for entry in pipeline("factory-image-build")["spec"]["tasks"]
            + pipeline("factory-image-build")["spec"]["finally"]
        }
        self.assertFalse({"attest", "promote"} & names)
