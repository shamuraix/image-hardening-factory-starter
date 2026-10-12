"""Environment values live only in deploy/environments/<env>/manifest/environment.yaml.

deploy/base, deploy/chains, and deploy/components are environment-neutral; each
environment build fills them from its manifest and differs from the example
environment only where documented.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ROOT / "deploy" / "environments"
KUSTOMIZE = shutil.which("kustomize") or shutil.which("kubectl")
PLACEHOLDER = "from-environment"


def _build(path: str) -> dict[tuple[str, str], dict]:
    if KUSTOMIZE is None:
        raise unittest.SkipTest("kustomize or kubectl is not installed")
    command = [KUSTOMIZE, "build", str(ROOT / path)]
    if KUSTOMIZE.endswith("kubectl"):
        command = [KUSTOMIZE, "kustomize", str(ROOT / path)]
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    documents = [d for d in yaml.safe_load_all(output) if d]
    return {(d["kind"], d["metadata"]["name"]): d for d in documents}


def _manifest(environment: str) -> dict[str, str]:
    path = ENVIRONMENTS / environment / "manifest" / "environment.yaml"
    return yaml.safe_load(path.read_text())["data"]


def _environment_names() -> list[str]:
    return sorted(p.parent.parent.name for p in ENVIRONMENTS.glob("*/manifest/environment.yaml"))


def _params(build: dict) -> dict[str, str]:
    spec = build[("Repository", "image-hardening-factory")]["spec"]
    return {p["name"]: p["value"] for p in spec["params"]}


class EnvironmentManifestTests(unittest.TestCase):
    def test_every_environment_defines_the_same_keys(self) -> None:
        expected = set(_manifest("example"))
        for environment in _environment_names():
            self.assertEqual(set(_manifest(environment)), expected, environment)

    def test_component_consumes_exactly_the_manifest_keys(self) -> None:
        component = yaml.safe_load(
            (ROOT / "deploy/components/environment/kustomization.yaml").read_text()
        )
        sources = [
            r["source"]["fieldPath"].removeprefix("data.") for r in component["replacements"]
        ]
        self.assertEqual(len(sources), len(set(sources)), "one replacement per key")
        self.assertEqual(set(sources), set(_manifest("example")))

    def test_environment_values_appear_only_in_their_manifest(self) -> None:
        # Hosts, URLs, repository paths, and image references (anything with a
        # dot or slash) unique to one environment must not leak into code.
        shared = set(_manifest("example").values())
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.split("\0")
        for environment in _environment_names():
            if environment == "example":
                continue
            values = {
                v
                for v in _manifest(environment).values()
                if v not in shared and ("." in v or "/" in v)
            }
            own = f"deploy/environments/{environment}/"
            for name in tracked:
                path = ROOT / name
                if not name or name.startswith(own) or not path.is_file():
                    continue
                text = path.read_text(errors="ignore")
                for value in values:
                    self.assertFalse(value in text, f"{environment} value {value!r} in {name}")

    def test_builds_leave_no_placeholders(self) -> None:
        for environment in _environment_names():
            for build in (
                f"deploy/environments/{environment}",
                f"deploy/environments/{environment}/chains",
            ):
                documents = _build(build)
                self.assertNotIn(PLACEHOLDER, yaml.safe_dump(list(documents.values())), build)
                self.assertNotIn(("ConfigMap", "factory-environment"), documents, build)


class ExampleEnvironmentTests(unittest.TestCase):
    def test_example_keeps_network_policies_and_agents_on(self) -> None:
        example = _build("deploy/environments/example")
        values = _manifest("example")
        self.assertIn(("NetworkPolicy", "default-deny"), example)
        self.assertEqual(
            example[("NetworkPolicy", "artifactory")]["spec"]["egress"][0]["to"][0]["ipBlock"],
            {"cidr": values["ARTIFACTORY_CIDR"]},
        )
        params = _params(example)
        self.assertEqual(params["enable_agents"], "true")
        self.assertEqual(params["git_provider"], values["GIT_PROVIDER"])
        self.assertEqual(params["runner_image"], values["RUNNER_IMAGE"])
        self.assertEqual(params["intake_runner_image"], values["INTAKE_RUNNER_IMAGE"])
        self.assertEqual(params["agent_image"], values["AGENT_IMAGE"])
        repository = example[("Repository", "image-hardening-factory")]["spec"]
        self.assertEqual(repository["url"], values["GIT_REPOSITORY_URL"])
        self.assertEqual(repository["git_provider"]["url"], values["GIT_PROVIDER_URL"])
        self.assertEqual(repository["git_provider"]["type"], values["GIT_PROVIDER"])
        for key, document in example.items():
            if key[0] == "CronJob":
                container = document["spec"]["jobTemplate"]["spec"]["template"]["spec"][
                    "containers"
                ][0]
                self.assertEqual(container["image"], values["RUNNER_IMAGE"], key)

    def test_settings_combine_base_and_manifest(self) -> None:
        example = _build("deploy/environments/example")
        settings = example[("ConfigMap", "factory-settings")]["data"]
        base = yaml.safe_load((ROOT / "deploy/base/settings.yaml").read_text())["data"]
        values = _manifest("example")
        for key, value in base.items():
            self.assertEqual(settings[key], value, key)
        injected = set(settings) - set(base)
        self.assertTrue(injected)
        for key in injected:
            self.assertEqual(settings[key], values[key], key)

    def test_chains_builder_id(self) -> None:
        chains = _build("deploy/environments/example/chains")
        config = chains[("ConfigMap", "chains-config")]
        self.assertEqual(config["metadata"]["namespace"], "tekton-chains")
        self.assertEqual(config["data"]["builder.id"], _manifest("example")["CHAINS_BUILDER_ID"])


class DevEnvironmentTests(unittest.TestCase):
    def test_dev_drops_only_network_policies_and_agents(self) -> None:
        example = _build("deploy/environments/example")
        dev = _build("deploy/environments/dev")
        self.assertFalse([k for k in dev if k[0] == "NetworkPolicy"])
        self.assertEqual(
            {k for k in example if k[0] != "NetworkPolicy"}, set(dev), "only NetworkPolicies differ"
        )
        self.assertEqual(_params(dev)["enable_agents"], "false")
        self.assertEqual(
            dev[("ValidatingAdmissionPolicy", "image-factory-pr-secret-isolation")],
            example[("ValidatingAdmissionPolicy", "image-factory-pr-secret-isolation")],
        )
        # Apart from manifest values, everything else is identical to example.
        for key, document in dev.items():
            if key[0] not in ("ConfigMap", "Repository", "CronJob"):
                self.assertEqual(document, example[key], key)

    def test_dev_uses_repository_path_prefixes(self) -> None:
        values = _manifest("dev")
        # The remote repository mirrors the CDN root; write_repo_config.sh
        # appends ubi<major>/<major>/<arch>/{baseos,appstream}/os.
        self.assertTrue(
            values["FACTORY_UBI_MIRROR_URL"].startswith(values["ARTIFACTORY_URL"] + "/")
        )
        self.assertTrue(
            values["FACTORY_UBI_MIRROR_URL"].endswith("/content/public/ubi/dist"),
        )
        # Repository-path addressing: every OCI repository is <key>/<prefix>
        # inside the one docker repository, so scripts build
        # ${ARTIFACTORY_REGISTRY}/${repository}/${path} unchanged.
        keys = (
            "UPSTREAM_OCI_REPOSITORY",
            "FACTORY_BASE_QUARANTINE_REPOSITORY",
            "FACTORY_APPLICATION_QUARANTINE_REPOSITORY",
            "FACTORY_RELEASE_REPOSITORY",
            "FACTORY_CANARY_REPOSITORY",
        )
        docker = values["UPSTREAM_OCI_REPOSITORY"].split("/", 1)[0] + "/"
        for key in keys:
            self.assertTrue(values[key].startswith(docker), key)
        prefixes = [
            values[k][len(docker) :]
            for k in (
                "FACTORY_BASE_QUARANTINE_REPOSITORY",
                "FACTORY_RELEASE_REPOSITORY",
                "UPSTREAM_OCI_REPOSITORY",
            )
        ]
        self.assertEqual(len(set(prefixes)), 3, "trust boundaries must not share a prefix")
        self.assertIn("/", values["FACTORY_SOURCE_REPOSITORY"])
        for key in ("RUNNER_IMAGE", "INTAKE_RUNNER_IMAGE", "AGENT_IMAGE"):
            self.assertRegex(values[key], r"@sha256:")
            self.assertTrue(values[key].startswith(f"{values['ARTIFACTORY_REGISTRY']}/{docker}"))
        settings = _build("deploy/environments/dev")[("ConfigMap", "factory-settings")]["data"]
        self.assertNotIn("FACTORY_RPM_SOURCE_MODE", settings)
        for key in keys:
            self.assertEqual(settings[key], values[key])


if __name__ == "__main__":
    unittest.main()
