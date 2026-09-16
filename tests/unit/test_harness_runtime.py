import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

spec = importlib.util.spec_from_file_location(
    "harness_runtime", Path(__file__).resolve().parents[1] / "integration/kind/runtime_config.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HarnessRuntimeTests(unittest.TestCase):
    def test_runtime_selection_is_validated_and_only_changes_marker(self):
        source = "spec:\n  hostUsers: false\n  # FACTORY_RUNTIME_CLASS\n"
        with TemporaryDirectory() as d:
            state = Path(d)
            self.assertEqual(module.render_pipeline(source, state), source)
            (state / "runtime-class").write_text("factory-crun-test\n")
            self.assertEqual(
                module.render_pipeline(source, state),
                source.replace("# FACTORY_RUNTIME_CLASS", "runtimeClassName: factory-crun-test"),
            )
            with self.assertRaises(ValueError):
                module.render_pipeline("missing marker", state)
            (state / "runtime-class").write_text("name\n  hostNetwork: true")
            with self.assertRaises(ValueError):
                module.render_pipeline(source, state)
