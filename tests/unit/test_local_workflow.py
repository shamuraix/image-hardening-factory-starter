import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LocalWorkflowTests(unittest.TestCase):
    def test_local_lock_is_marked_and_rejected_by_importer(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        importer = (ROOT / "scripts/import_image.sh").read_text(encoding="utf-8")

        self.assertIn("localDevelopment:true", local_build)
        self.assertIn(".localDevelopment != true", importer)

    def test_local_services_are_bound_to_loopback(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")

        self.assertIn("--bind 127.0.0.1", local_build)
        self.assertIn('--publish "127.0.0.1:', local_build)

    def test_upstream_ubi_repositories_remain_local_only(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        production_prepare = (ROOT / "scripts/prepare_context.sh").read_text(encoding="utf-8")

        self.assertIn("LOCAL_USE_UPSTREAM_UBI_REPOS", local_build)
        self.assertNotIn("LOCAL_USE_UPSTREAM_UBI_REPOS", production_prepare)

    def test_repo_mount_does_not_make_yum_directory_read_only(self) -> None:
        build = (ROOT / "scripts/build_image.sh").read_text(encoding="utf-8")

        self.assertIn("/factory.repo:/etc/yum.repos.d/factory.repo:ro", build)
        self.assertNotIn("repo_dir}:/etc/yum.repos.d:ro", build)

    def test_archive_and_test_runner_use_exact_image_reference(self) -> None:
        build = (ROOT / "scripts/build_image.sh").read_text(encoding="utf-8")
        test_runner = (ROOT / "scripts/run_tests.sh").read_text(encoding="utf-8")

        self.assertIn("oci-archive:${work_dir}/image.oci.tar:${local_ref}", build)
        self.assertIn(
            'skopeo inspect "oci-archive:${work_dir}/image.oci.tar:${local_ref}"',
            build,
        )
        self.assertIn("image=${LOCAL_IMAGE_REF}", test_runner)
        self.assertNotIn("<none>", test_runner)

    def test_application_rpm_family_comes_from_catalog_base(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")

        self.assertIn("catalog_base_image=$(yq -r '.build.base.image'", local_build)
        self.assertIn('rpm_catalog="catalog/images/${base_image}.yaml"', local_build)
        self.assertIn('export RPM_SNAPSHOT_UBI9_ID="${snapshot_id}"', local_build)
        self.assertIn('export RPM_SNAPSHOT_UBI10_ID="${snapshot_id}"', local_build)
        self.assertNotIn('.product.version\' "${catalog}") == 10.*', local_build)

    def test_local_base_override_is_recorded_as_development_only(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")

        self.assertIn("LOCAL_BASE_IMAGE_OVERRIDE", local_build)
        self.assertIn("developmentBaseOverride", local_build)
        self.assertIn("localDevelopment:true", local_build)

    def test_atlassian_overlays_guard_forced_removals_on_ubi10(self) -> None:
        for image in ("bitbucket-lts", "confluence-lts", "jira-lts"):
            patch = (
                ROOT / "overlays" / image / "patches" / "0002-ubi10-compatibility.patch"
            ).read_text(encoding="utf-8")
            self.assertIn("ARG BASE_MAJOR=9", patch)
            self.assertIn('[ "${BASE_MAJOR}" = "9" ]', patch)

    def test_rpm_integrity_has_ubi10_configuration(self) -> None:
        verifier = (ROOT / "scripts/assert_rpm_integrity.sh").read_text(encoding="utf-8")
        ubi10_allowlist = ROOT / "tests/profiles/base/rpm-verify.ubi10.allow"

        self.assertIn("rpm -Va --nomtime", verifier)
        self.assertTrue(ubi10_allowlist.is_file())
        self.assertIn("/usr/share/crypto-policies/FIPS/java.txt", ubi10_allowlist.read_text())

    def test_http_readiness_suppresses_transient_errors(self) -> None:
        wait_http = (ROOT / "scripts/wait_http.sh").read_text(encoding="utf-8")

        self.assertIn(">/dev/null 2>&1; do", wait_http)
        self.assertIn("timed out waiting for", wait_http)
