"""deploy/base and deploy/overlays/local build and differ only where documented."""

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KUSTOMIZE = shutil.which("kustomize") or shutil.which("kubectl")


def _build(path: str) -> dict[tuple[str, str], dict]:
    if KUSTOMIZE is None:
        raise unittest.SkipTest("kustomize or kubectl is not installed")
    command = [KUSTOMIZE, "build", str(ROOT / path)]
    if KUSTOMIZE.endswith("kubectl"):
        command = [KUSTOMIZE, "kustomize", str(ROOT / path)]
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    documents = [d for d in yaml.safe_load_all(output) if d]
    return {(d["kind"], d["metadata"]["name"]): d for d in documents}


class DeployOverlayTests(unittest.TestCase):
    def test_base_keeps_network_policies_and_agents_on(self) -> None:
        base = _build("deploy/base")
        self.assertIn(("NetworkPolicy", "default-deny"), base)
        params = {
            p["name"]: p["value"]
            for p in base[("Repository", "image-hardening-factory")]["spec"]["params"]
        }
        self.assertEqual(params["enable_agents"], "true")
        self.assertEqual(params["git_provider"], "gitlab")

    def test_local_overlay_drops_only_network_policies_and_agents(self) -> None:
        base = _build("deploy/base")
        local = _build("deploy/overlays/local")
        self.assertFalse([k for k in local if k[0] == "NetworkPolicy"])
        self.assertEqual(
            {k for k in base if k[0] != "NetworkPolicy"}, set(local), "only NetworkPolicies differ"
        )
        params = {
            p["name"]: p["value"]
            for p in local[("Repository", "image-hardening-factory")]["spec"]["params"]
        }
        self.assertEqual(params["enable_agents"], "false")
        for name in ("runner_image", "intake_runner_image", "agent_image", "git_provider"):
            self.assertIn(name, params)
        # Everything else is byte-identical to base.
        for key, document in local.items():
            if key[0] != "Repository":
                self.assertEqual(document, base[key], key)
        self.assertEqual(
            local[("ValidatingAdmissionPolicy", "image-factory-pr-secret-isolation")],
            base[("ValidatingAdmissionPolicy", "image-factory-pr-secret-isolation")],
        )


if __name__ == "__main__":
    unittest.main()


class DevOverlayTests(unittest.TestCase):
    def test_dev_overlay_only_repoints_artifactory_and_runner_images(self) -> None:
        local = _build("deploy/overlays/local")
        dev = _build("deploy/overlays/dev")
        self.assertEqual(set(local), set(dev))
        settings = dev[("ConfigMap", "factory-settings")]["data"]
        base_settings = local[("ConfigMap", "factory-settings")]["data"]
        changed = {k for k in settings if settings[k] != base_settings.get(k)}
        self.assertEqual(
            changed,
            {
                "INTERNAL_GIT_BASE_URL",
                "FACTORY_GITLAB_API_URL",
                "ARTIFACTORY_URL",
                "ARTIFACTORY_REGISTRY",
                "ARTIFACTORY_USERNAME",
                "FACTORY_SOURCE_REPOSITORY",
                "UPSTREAM_OCI_REPOSITORY",
                "FACTORY_BASE_QUARANTINE_REPOSITORY",
                "FACTORY_APPLICATION_QUARANTINE_REPOSITORY",
                "FACTORY_RELEASE_REPOSITORY",
                "FACTORY_CANARY_REPOSITORY",
                "FACTORY_UBI_MIRROR_URL",
            },
        )
        # The remote repository mirrors the CDN root; write_repo_config.sh
        # appends ubi<major>/<major>/<arch>/{baseos,appstream}/os.
        self.assertEqual(
            settings["FACTORY_UBI_MIRROR_URL"],
            "https://artifactory.cicd.dc/artifactory/ext-redhat-ubi-remote/content/public/ubi/dist",
        )
        self.assertNotIn("FACTORY_RPM_SOURCE_MODE", settings)
        # Repository-path addressing: every OCI repository is <key>/<prefix>
        # inside the one docker repository, so scripts build
        # ${ARTIFACTORY_REGISTRY}/${repository}/${path} unchanged.
        docker = "techops-cicd-esd-hip-docker-dev-local/"
        for key in (
            "UPSTREAM_OCI_REPOSITORY",
            "FACTORY_BASE_QUARANTINE_REPOSITORY",
            "FACTORY_APPLICATION_QUARANTINE_REPOSITORY",
            "FACTORY_RELEASE_REPOSITORY",
            "FACTORY_CANARY_REPOSITORY",
        ):
            self.assertTrue(settings[key].startswith(docker), key)
        prefixes = [
            settings[k][len(docker) :]
            for k in (
                "FACTORY_BASE_QUARANTINE_REPOSITORY",
                "FACTORY_RELEASE_REPOSITORY",
                "UPSTREAM_OCI_REPOSITORY",
            )
        ]
        self.assertEqual(len(set(prefixes)), 3, "trust boundaries must not share a prefix")
        self.assertTrue(
            settings["FACTORY_SOURCE_REPOSITORY"].startswith(
                "techops-cicd-esd-hip-generic-dev-local/"
            )
        )
        params = {
            p["name"]: p["value"]
            for p in dev[("Repository", "image-hardening-factory")]["spec"]["params"]
        }
        for name in ("runner_image", "intake_runner_image", "agent_image"):
            self.assertRegex(
                params[name],
                r"^artifactory\.cicd\.dc/techops-cicd-esd-hip-docker-dev-local/factory/[a-z-]+@sha256:",
            )
        self.assertEqual(params["enable_agents"], "false")
        repository = dev[("Repository", "image-hardening-factory")]["spec"]
        self.assertEqual(
            repository["url"],
            "https://gitlab.cicd.dc/techops-cicd/cicd-federal/govcloud-image-hardening-factory",
        )
        self.assertEqual(repository["git_provider"]["url"], "https://gitlab.cicd.dc")
        self.assertEqual(repository["git_provider"]["type"], "gitlab")
        for key, document in dev.items():
            if key[0] not in ("ConfigMap", "Repository"):
                self.assertEqual(document, local[key], key)
