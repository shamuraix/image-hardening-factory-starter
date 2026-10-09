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
