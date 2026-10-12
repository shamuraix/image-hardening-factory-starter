"""Pins that live in more than one place must agree.

vendir and the catalog name the same Iron Bank commits; Renovate must see every
tool pin; Jinja2 must match the release base's python3-jinja2 everywhere it is
pinned; RPM-installed tools are checked by the runner build.
"""

import json
import re
import tomllib
import unittest
from pathlib import Path

import yaml

from factory.catalog import load_catalog
from factory.context_check import BASE_JINJA2

ROOT = Path(__file__).resolve().parents[2]


class PinConsistencyTests(unittest.TestCase):
    def test_vendir_matches_catalog_sources(self) -> None:
        vendir = yaml.safe_load((ROOT / "vendir/config.yml").read_text())
        refs = {
            content["path"]: content["git"]
            for directory in vendir["directories"]
            for content in directory["contents"]
        }
        images = load_catalog(ROOT / "catalog/images")
        self.assertEqual(set(refs), set(images), "vendir and catalog list different images")
        for name, image in images.items():
            with self.subTest(image=name):
                self.assertEqual(refs[name]["ref"], image.data["source"]["revision"])
                self.assertEqual(refs[name]["url"], image.data["source"]["upstream"])

    def test_renovate_sees_every_tool_pin(self) -> None:
        lock = (ROOT / "tools/versions.lock.yaml").read_text()
        config = json.loads((ROOT / "renovate.json").read_text())
        pattern = re.sub(r"\(\?<(\w+)>", r"(?P<\1>", config["customManagers"][0]["matchStrings"][0])
        tracked = {m.group("depName"): m.group("currentValue") for m in re.finditer(pattern, lock)}
        tools = yaml.safe_load(lock)["tools"]
        for name, entry in tools.items():
            with self.subTest(tool=name):
                project = entry["project"].removeprefix("https://github.com/")
                self.assertEqual(tracked.get(project), str(entry["version"]))

    def test_renovate_leaves_base_coupled_pins_alone(self) -> None:
        rules = json.loads((ROOT / "renovate.json").read_text())["packageRules"]
        disabled = {
            name
            for rule in rules
            if rule.get("enabled") is False
            for name in rule.get("matchPackageNames", []) + rule.get("matchDepNames", [])
        }
        self.assertTrue({"Jinja2", "MarkupSafe", "containers/skopeo"} <= disabled)

    def test_jinja2_pin_matches_the_release_base(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        pinned = [d for d in pyproject["project"]["dependencies"] if d.startswith("Jinja2")]
        self.assertEqual(len(pinned), 1)
        version = pinned[0].split("==")[1]
        build_dist = (ROOT / "toolchain/build-dist.sh").read_text()
        self.assertIn(f"'Jinja2=={version}'", build_dist)
        self.assertRegex(build_dist, r"'MarkupSafe==\d+\.\d+\.\d+'")
        images = load_catalog(ROOT / "catalog/images")
        app_base = {image.base_image for image in images.values() if image.base_image}
        self.assertEqual(len(app_base), 1, "apps should share one base")
        base_major = str(images[app_base.pop()].data["product"]["version"]).split(".")[0]
        self.assertTrue(version.startswith(BASE_JINJA2[base_major] + "."))

    def test_runner_checks_rpm_installed_tool_pins(self) -> None:
        build_dist = (ROOT / "toolchain/build-dist.sh").read_text()
        containerfile = (ROOT / "toolchain/Containerfile.factory-runner").read_text()
        self.assertIn('for name in ("skopeo",)', build_dist)
        self.assertIn("COPY dist/rpm-versions.txt", containerfile)
        self.assertIn("does not match tools/versions.lock.yaml", containerfile)


if __name__ == "__main__":
    unittest.main()
