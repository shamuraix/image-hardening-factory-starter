import importlib.util
import tomllib
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "crun_probe", Path(__file__).resolve().parents[1] / "integration/kind/probe-crun.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CrunConfigTests(unittest.TestCase):
    def test_new_handler_preserves_default_and_existing_config(self):
        for version, plugin in [
            (2, "io.containerd.grpc.v1.cri"),
            (3, "io.containerd.cri.v1.runtime"),
        ]:
            with self.subTest(version=version):
                original = f'''version = {version}
[plugins."{plugin}".containerd]
default_runtime_name = "runc"
[plugins."{plugin}".containerd.runtimes.runc]
runtime_type = "io.containerd.runc.v2"
'''
                result = tomllib.loads(module.crun_config(original, "factory-crun-test"))
                runtimes = result["plugins"][plugin]["containerd"]["runtimes"]
                candidate = runtimes.pop("factory-crun-test")
                self.assertEqual(candidate["options"]["BinaryName"], "/usr/bin/crun")
                self.assertEqual(result, tomllib.loads(original))
                with self.assertRaises(ValueError):
                    module.crun_config(
                        module.crun_config(original, "factory-crun-test"), "factory-crun-test"
                    )

    def test_unknown_version_is_rejected(self):
        with self.assertRaises(ValueError):
            module.crun_config("version = 1", "factory-crun-test")

    def test_waits_for_matching_runtime_and_user_namespace_capability(self):
        self.assertFalse(module.handler_ready({}, "factory-crun-test"))
        self.assertFalse(
            module.handler_ready(
                {"runtimeHandlers": [{"name": "runc", "features": {"userNamespaces": True}}]},
                "factory-crun-test",
            )
        )
        for feature in (None, False, True):
            status = {
                "runtimeHandlers": [
                    {"name": "factory-crun-test", "features": {"userNamespaces": feature}}
                ]
            }
            self.assertEqual(module.handler_ready(status, "factory-crun-test"), feature is True)
