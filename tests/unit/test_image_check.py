"""image-check reads the OCI archive statically; required checks block, parity checks inform."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from factory.image_check import ImageFS, check_image, load_image
from tests.unit import oci_fixture

ROOT = Path(__file__).resolve().parents[2]
JVM = "/usr/lib/jvm/java-21-openjdk-21.0.12"

BASE_LAYER = [
    ("/etc", "dir", 0o755, 0, 0, ""),
    (
        "/etc/passwd",
        "file",
        0o644,
        0,
        0,
        b"root:x:0:0::/root:/bin/bash\njira:x:2001:2001::/var/atlassian/application-data/jira:/bin/bash\n",
    ),
    ("/usr", "dir", 0o755, 0, 0, ""),
    ("/usr/bin", "dir", 0o755, 0, 0, ""),
    ("/usr/bin/python3", "file", 0o755, 0, 0, b""),
    ("/usr/lib/python3.12/site-packages/jinja2", "dir", 0o755, 0, 0, ""),
    ("/usr/lib/python3.12/site-packages/jinja2/__init__.py", "file", 0o644, 0, 0, b""),
    # RHEL alternatives chain: absolute then relative links.
    ("/etc/alternatives", "dir", 0o755, 0, 0, ""),
    ("/etc/alternatives/java", "symlink", 0o777, 0, 0, f"{JVM}/bin/java"),
    ("/usr/bin/java", "symlink", 0o777, 0, 0, "../../etc/alternatives/java"),
    (f"{JVM}/bin/java", "file", 0o755, 0, 0, b""),
    ("/usr/bin/tini", "file", 0o755, 0, 0, b""),
    ("/entrypoint.py", "file", 0o755, 2001, 2001, b""),
    ("/entrypoint_helpers.py", "file", 0o644, 2001, 2001, b""),
    ("/etc/container_id", "file", 0o644, 2001, 2001, b""),
    ("/opt/atlassian/etc/server.xml.j2", "file", 0o644, 0, 0, b""),
    ("/opt/atlassian/jira", "dir", 0o755, 2001, 2001, ""),
    ("/var/atlassian/application-data/jira", "dir", 0o750, 2001, 2001, ""),
    ("/opt/jira-servicedesk-application-11.obr", "file", 0o644, 0, 0, b""),
]
CLEANUP_LAYER = [("/opt/jira-servicedesk-application-11.obr", "whiteout", 0, 0, 0, "")]
CONFIG = {
    "config": {
        "User": "jira",
        "Entrypoint": ["/usr/bin/tini", "--"],
        "Cmd": ["/entrypoint.py"],
        "ExposedPorts": {"8080/tcp": {}, "40001/tcp": {}},
        "Volumes": {"/var/atlassian/application-data/jira": {}},
        "Env": [
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "RUN_USER=jira",
            "RUN_UID=2001",
            "JIRA_HOME=/var/atlassian/application-data/jira",
            "JIRA_INSTALL_DIR=/opt/atlassian/jira",
        ],
    }
}


def _results(report):
    return {check.id: check for check in report.checks}


class ImageCheckTests(unittest.TestCase):
    def run_check(self, config=CONFIG, layers=None, sbom=None):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = oci_fixture.build(
                root / "image.oci.tar", config, layers or [BASE_LAYER, CLEANUP_LAYER]
            )
            sbom_path = None
            if sbom is not None:
                sbom_path = root / "sbom.json"
                sbom_path.write_text(json.dumps(sbom))
            return check_image(ROOT / "catalog/images/jira-lts.yaml", image, sbom_path)

    def test_conforming_image_passes_required_and_reports_parity(self):
        report = self.run_check()
        failed = [c.id for c in report.checks if c.severity == "required" and not c.passed]
        self.assertEqual(failed, [], report.to_dict())
        results = _results(report)
        self.assertEqual(results["on-path:java"].detail, "/usr/bin/java")
        # Iron Bank's 0755 install dir and app-owned entrypoint are parity findings only.
        self.assertFalse(results["parity:install-dir"].passed)
        self.assertFalse(results["parity:root-owned:/entrypoint.py"].passed)
        self.assertTrue(report.passed)

    def test_whiteout_removes_lower_layer_build_artefacts(self):
        self.assertTrue(_results(self.run_check())["absent:/opt/*.obr"].passed)
        report = self.run_check(layers=[BASE_LAYER])
        self.assertFalse(_results(report)["absent:/opt/*.obr"].passed)
        self.assertFalse(report.passed)

    def test_root_user_wrong_ports_and_missing_volume_fail(self):
        config = json.loads(json.dumps(CONFIG))
        config["config"].update({"User": "root", "ExposedPorts": {"7991/tcp": {}}, "Volumes": {}})
        results = _results(self.run_check(config=config))
        for check in ("user", "exposed-ports", "home-volume"):
            self.assertFalse(results[check].passed, check)

    def test_missing_jinja2_and_broken_java_link_fail(self):
        layer = [
            item for item in BASE_LAYER if "jinja2" not in item[0] and item[0] != f"{JVM}/bin/java"
        ]
        results = _results(self.run_check(layers=[layer, CLEANUP_LAYER]))
        self.assertFalse(results["python-jinja2"].passed)
        self.assertFalse(results["on-path:java"].passed)

    def test_opaque_directory_hides_lower_contents(self):
        with TemporaryDirectory() as directory:
            image = oci_fixture.build(
                Path(directory) / "i.tar",
                CONFIG,
                [
                    [("/opt/x", "dir", 0o755, 0, 0, ""), ("/opt/x/old", "file", 0o644, 0, 0, b"")],
                    [("/opt/x", "opaque", 0, 0, 0, ""), ("/opt/x/new", "file", 0o644, 0, 0, b"")],
                ],
            )
            _config, fs = load_image(image)
        self.assertNotIn("/opt/x/old", fs.entries)
        self.assertIn("/opt/x/new", fs.entries)

    def test_dot_files_keep_their_names(self):
        fs = ImageFS()
        import io
        import tarfile

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo("./root/.bashrc")
            tar.addfile(info, io.BytesIO(b""))
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as tar:
            fs.apply_layer(tar)
        self.assertIn("/root/.bashrc", fs.entries)

    def test_bitbucket_rejects_banned_distro_git(self):
        catalog = yaml.safe_load((ROOT / "catalog/images/bitbucket-lts.yaml").read_text())
        self.assertEqual(catalog["verify"]["profile"], "bitbucket")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = oci_fixture.build(root / "i.tar", CONFIG, [BASE_LAYER])
            sbom = root / "sbom.json"
            sbom.write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "name": "git",
                                "version": "2.52.0-1.el10",
                                "purl": "pkg:rpm/redhat/git@2.52.0-1.el10",
                            }
                        ]
                    }
                )
            )
            report = check_image(ROOT / "catalog/images/bitbucket-lts.yaml", image, sbom)
        self.assertFalse(_results(report)["no-banned-rpm-git"].passed)

    def test_base_images_without_verify_block_are_skipped(self):
        with TemporaryDirectory() as directory:
            image = oci_fixture.build(Path(directory) / "i.tar", CONFIG, [BASE_LAYER])
            report = check_image(ROOT / "catalog/images/ubi10-minimal.yaml", image)
        self.assertTrue(report.passed)
        self.assertEqual([c.id for c in report.checks], ["profile"])


if __name__ == "__main__":
    unittest.main()
