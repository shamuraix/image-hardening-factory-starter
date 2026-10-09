"""Invariants for the Tekton / Pipelines-as-Code definitions.

These tests encode the trust model so a change that, for example, binds a write
secret to a pull-request run or skips seal verification fails CI.
"""

from __future__ import annotations

import re
import unittest

import yaml

from factory import tekton
from tests.unit.tekton_support import (
    ROOT,
    documents,
    param,
    pipeline,
    pipeline_runs,
    secret_names,
    step,
    task,
)

# Verified against tektoncd/pipelines-as-code pkg/apis/pipelinesascode/keys/keys.go.
PAC_TRIGGER_KEYS = {
    "on-event",
    "on-comment",
    "on-target-branch",
    "on-path-change",
    "on-path-change-ignore",
    "on-label",
    "on-cel-expression",
    "max-keep-runs",
    "cancel-in-progress",
    "task",
    "pipeline",
}
READ_ONLY_SECRETS = {
    "factory-artifactory-read",
    "factory-rpm-mirror",
    "factory-intake-cosign-public-key",
    "factory-ai-gateway",
}
CREDENTIALED_TASKS_FORBIDDEN_ON_PR = {
    "factory-quarantine",
    "factory-attest",
    "factory-promote",
    "factory-intake",
    "factory-trigger-dependents",
    "factory-release-resolve",
}
ENVIRONMENTS = ("commercial", "gov1", "gov2")


def _pipeline_tasks(name: str) -> list[dict]:
    spec = pipeline(name)["spec"]
    return spec.get("tasks", []) + spec.get("finally", [])


def _is_pull_request_run(run: dict) -> bool:
    annotations = run["metadata"]["annotations"]
    cel = annotations.get("pipelinesascode.tekton.dev/on-cel-expression", "")
    return (
        'event == "pull_request"' in cel
        or "pull_request" in annotations.get("pipelinesascode.tekton.dev/on-event", "")
        or "pipelinesascode.tekton.dev/on-comment" in annotations
    )


def _run_params(run: dict) -> dict[str, str]:
    return {item["name"]: item["value"] for item in run["spec"]["params"]}


def _expand(name: str) -> set[str]:
    if name == "factory-cosign-$(params.environment)":
        return {f"factory-cosign-{environment}" for environment in ENVIRONMENTS}
    if name == "$(params.scm-secret)":
        return {"factory-scm-bot"}
    if name == "$(params.git-auth-secret)":
        return set()
    return {name}


class GeneratedPipelineRunTests(unittest.TestCase):
    def test_generated_pipeline_runs_have_not_drifted(self) -> None:
        rendered = tekton.render(ROOT / "catalog/images")
        self.assertEqual(tekton.drift(rendered, ROOT / ".tekton"), [])

    def test_every_atlassian_image_has_pr_push_schedule_and_base_release_runs(self) -> None:
        runs = pipeline_runs()
        for image in ("bitbucket-lts", "jira-lts", "confluence-lts"):
            for suffix in ("on-pull-request", "on-push", "on-schedule", "on-base-release"):
                self.assertIn(f"{image}-{suffix}", runs)

    def test_image_path_filters_cover_overlay_catalog_and_profile(self) -> None:
        cel = pipeline_runs()["confluence-lts-on-pull-request"]["metadata"]["annotations"][
            "pipelinesascode.tekton.dev/on-cel-expression"
        ]
        for path in (
            "catalog/images/confluence-lts.yaml",
            "overlays/confluence-lts/patches/**",
            "tests/profiles/confluence/**",
            ".tekton/tasks/**",
        ):
            self.assertIn(f'"{path}".pathChanged()', cel)
        # A base change must not rebuild apps against an unreleased base.
        self.assertNotIn("overlays/ubi9-minimal", cel)

    def test_only_known_pac_annotations_are_used(self) -> None:
        for name, run in pipeline_runs().items():
            for key in run["metadata"].get("annotations", {}):
                if key.startswith("pipelinesascode.tekton.dev/"):
                    self.assertIn(key.split("/", 1)[1], PAC_TRIGGER_KEYS, f"{name}: {key}")


class ReferenceTests(unittest.TestCase):
    def test_every_reference_resolves(self) -> None:
        tasks = documents("Task")
        pipelines = documents("Pipeline")
        for name in pipelines:
            for entry in _pipeline_tasks(name):
                self.assertIn(entry["taskRef"]["name"], tasks, f"{name}/{entry['name']}")
        for name, run in pipeline_runs().items():
            self.assertIn(run["spec"]["pipelineRef"]["name"], pipelines, name)

    def test_every_pipeline_task_has_an_explicit_service_account(self) -> None:
        accounts = {
            document["metadata"]["name"]
            for document in yaml.safe_load_all(
                (ROOT / "deploy/base/serviceaccounts.yaml").read_text(encoding="utf-8")
            )
        }
        for name, run in pipeline_runs().items():
            specs = {item["pipelineTaskName"]: item for item in run["spec"]["taskRunSpecs"]}
            for entry in _pipeline_tasks(run["spec"]["pipelineRef"]["name"]):
                self.assertIn(entry["name"], specs, f"{name}: {entry['name']} has no SA")
            for item in specs.values():
                self.assertIn(item["serviceAccountName"], accounts)
            self.assertIn(run["spec"]["taskRunTemplate"]["serviceAccountName"], accounts)

    def test_network_policies_name_real_pipeline_tasks(self) -> None:
        known = {entry["name"] for name in documents("Pipeline") for entry in _pipeline_tasks(name)}
        for policy in yaml.safe_load_all(
            (ROOT / "deploy/base/network-policies.yaml").read_text(encoding="utf-8")
        ):
            selector = policy["spec"]["podSelector"]
            for expression in selector.get("matchExpressions", []):
                if expression["key"] == "tekton.dev/pipelineTask":
                    self.assertTrue(set(expression["values"]) <= known, policy["metadata"]["name"])


class PullRequestIsolationTests(unittest.TestCase):
    def test_pull_request_runs_never_publish_or_bind_the_scm_bot(self) -> None:
        for name, run in pipeline_runs().items():
            if not _is_pull_request_run(run):
                continue
            params = _run_params(run)
            self.assertNotIn("scm-secret", params, name)
            self.assertNotEqual(params.get("publish"), "true", name)
            self.assertNotEqual(params.get("enable-remediation"), "true", name)
            self.assertNotEqual(params.get("enable-release-request"), "true", name)
            self.assertIn(
                run["spec"]["pipelineRef"]["name"],
                {"factory-image-build", "factory-checks", "factory-agent-maintenance"},
                name,
            )

    def test_credentialed_tasks_in_pr_reachable_pipelines_are_publish_gated(self) -> None:
        for name in ("factory-image-build", "factory-checks", "factory-agent-maintenance"):
            for entry in _pipeline_tasks(name):
                if entry["taskRef"]["name"] in CREDENTIALED_TASKS_FORBIDDEN_ON_PR:
                    guards = [item["input"] for item in entry.get("when", [])]
                    self.assertIn("$(params.publish)", guards, f"{name}/{entry['name']}")

    def test_agent_scm_secret_defaults_to_nothing(self) -> None:
        params = {item["name"]: item for item in task("factory-claude-agent")["spec"]["params"]}
        self.assertEqual(params["scm-secret"]["default"], "factory-scm-disabled")
        for name in ("factory-image-build", "factory-agent-maintenance"):
            pipeline_params = {item["name"]: item for item in pipeline(name)["spec"]["params"]}
            self.assertEqual(pipeline_params["scm-secret"]["default"], "factory-scm-disabled")

    def test_admission_policy_fails_closed_for_every_non_push_event(self) -> None:
        # GitLab reports merge requests as "Merge Request", not "pull_request",
        # so the policy must allowlist trusted events rather than deny one name.
        text = (ROOT / "deploy/base/admission-policy.yaml").read_text(encoding="utf-8")
        self.assertIn("variables.eventType in ['push', 'incoming']", text)
        self.assertNotIn("!= 'pull_request'", text)
        self.assertEqual(text.count("variables.trusted ||"), 3)
        self.assertIn("validationActions: [Deny, Audit]", text)

    def test_admission_policy_covers_every_protected_secret(self) -> None:
        text = (ROOT / "deploy/base/admission-policy.yaml").read_text(encoding="utf-8")
        listed = set(re.findall(r"'(factory-[a-z0-9-]+)'", text))
        used: set[str] = set()
        for name in documents("Task"):
            for secret in secret_names(name):
                used |= _expand(secret)
        protected = used - READ_ONLY_SECRETS
        self.assertTrue(protected, "expected protected secrets in tasks")
        self.assertEqual(protected - listed, set())


class TaskHygieneTests(unittest.TestCase):
    def test_step_images_are_parameters(self) -> None:
        for name, document in documents("Task").items():
            spec = document["spec"]
            default = spec.get("stepTemplate", {}).get("image")
            for item in spec["steps"]:
                image = item.get("image", default)
                self.assertTrue(image and image.startswith("$(params."), f"{name}/{item['name']}")

    def test_stage_tasks_verify_inputs_before_running(self) -> None:
        for name, document in documents("Task").items():
            if "inputs" not in {item["name"] for item in document["spec"].get("params", [])}:
                continue
            first = document["spec"]["steps"][0]
            self.assertEqual(first["name"], "verify", name)
            self.assertIn("artifacts.sh verify-source", first["script"], name)
            self.assertIn('artifacts.sh verify "$@"', first["script"], name)

    def test_secrets_are_scoped_to_steps_not_templates_or_workspaces(self) -> None:
        for name, document in documents("Task").items():
            spec = document["spec"]
            for env in spec.get("stepTemplate", {}).get("env", []):
                self.assertNotIn("valueFrom", env, f"{name}: secret in stepTemplate")
            for workspace in spec.get("workspaces", []):
                if name != "factory-checkout":
                    self.assertNotIn("auth", workspace["name"], name)

    def test_parameterized_secret_names_never_default_to_empty(self) -> None:
        # An empty secretKeyRef/secret name is invalid even when optional, so a
        # task run without the param would fail at pod creation.
        for name, document in documents("Task").items():
            defaults = {
                item["name"]: item.get("default") for item in document["spec"].get("params", [])
            }
            for secret in secret_names(name):
                for param_name in re.findall(r"\$\(params\.([a-z0-9-]+)\)", secret):
                    if param_name in defaults and defaults[param_name] is not None:
                        self.assertTrue(defaults[param_name], f"{name}: {param_name}")

    def test_agent_credential_steps_never_run_workspace_code(self) -> None:
        report = step("factory-claude-agent", "report")["script"]
        self.assertNotIn("scripts/", report)
        publish = step("factory-claude-agent", "publish")["script"]
        self.assertIn("fetch --quiet --depth 1", publish)
        self.assertIn('cd "${broker_root}"', publish)
        self.assertNotIn("workspaces.shared.path", publish)
        agent = step("factory-claude-agent", "agent")
        self.assertEqual(
            {
                env["valueFrom"]["secretKeyRef"]["name"]
                for env in agent["env"]
                if "valueFrom" in env
            },
            {"factory-ai-gateway"},
        )
        for name in ("context", "validate"):
            self.assertNotIn(
                "valueFrom", str(step("factory-claude-agent", name).get("env", [])), name
            )

    def test_rootless_run_step_is_the_only_relaxed_container(self) -> None:
        for name, document in documents("Task").items():
            for item in document["spec"]["steps"]:
                context = item.get("securityContext", {})
                relaxed = context.get("allowPrivilegeEscalation") or "procMount" in context
                if relaxed:
                    self.assertIn(name, {"factory-rootless-build", "factory-rootless-test"})
                    self.assertEqual(item["name"], "run")
                    self.assertEqual(context.get("procMount"), "Unmasked")
                self.assertFalse(context.get("privileged", False), f"{name}/{item['name']}")

    def test_rootless_stages_run_in_a_pod_user_namespace(self) -> None:
        for run_name, run in pipeline_runs().items():
            if run["spec"]["pipelineRef"]["name"] != "factory-image-build":
                continue
            templates = {
                spec["pipelineTaskName"]: spec.get("podTemplate", {})
                for spec in run["spec"]["taskRunSpecs"]
            }
            for stage in ("build", "test"):
                self.assertIs(templates[stage].get("hostUsers"), False, f"{run_name}/{stage}")
            for stage, template in templates.items():
                if stage not in ("build", "test"):
                    self.assertNotIn("hostUsers", template, f"{run_name}/{stage}")

    def test_quarantine_emits_chains_type_hints(self) -> None:
        results = {item["name"] for item in task("factory-quarantine")["spec"]["results"]}
        self.assertTrue({"IMAGE_URL", "IMAGE_DIGEST", "EVIDENCE_URL", "EVIDENCE_SHA256"} <= results)

    def test_release_pipeline_records_merger_as_approver(self) -> None:
        resolve = next(
            entry
            for entry in _pipeline_tasks("factory-image-release")
            if entry["name"] == "resolve"
        )
        self.assertEqual(param(resolve, "sender"), "$(params.sender)")
        run = pipeline_runs()["release-on-push"]
        self.assertEqual(_run_params(run)["sender"], "{{ sender }}")
        self.assertIn(
            '"releases/**".pathChanged()',
            run["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-cel-expression"],
        )

    def test_repository_loads_definitions_from_default_branch(self) -> None:
        repository = yaml.safe_load((ROOT / "deploy/base/repository.yaml").read_text())
        self.assertEqual(repository["spec"]["settings"]["pipelinerun_provenance"], "default_branch")


if __name__ == "__main__":
    unittest.main()
