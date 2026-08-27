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

    def test_internal_ubi_cache_remains_local_only(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        production_prepare = (ROOT / "scripts/prepare_context.sh").read_text(encoding="utf-8")

        self.assertIn("LOCAL_RPM_CACHE_REPOSITORY", local_build)
        self.assertIn("LOCAL_RPM_CACHE_REPOSITORY is required", local_build)
        self.assertIn("FACTORY_UBI_REPO_PREFIX", local_build)
        self.assertNotIn("FACTORY_UBI_REPO_PREFIX", production_prepare)
        self.assertNotIn("cdn-ubi.redhat.com", local_build)

    def test_repo_mount_masks_upstream_repositories_and_remains_writable(self) -> None:
        build = (ROOT / "scripts/build_image.sh").read_text(encoding="utf-8")

        self.assertIn('"${repo_dir}:/etc/yum.repos.d:Z"', build)
        self.assertNotIn("repo_dir}:/etc/yum.repos.d:ro", build)
        self.assertNotIn("/factory.repo:/etc/yum.repos.d/factory.repo", build)
        self.assertIn("cannot fall back", build)

    def test_local_build_handles_new_skopeo_and_selinux_hosts(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        build = (ROOT / "scripts/build_image.sh").read_text(encoding="utf-8")

        self.assertIn("--remove-signatures", local_build)
        self.assertIn("--retry-times 5", local_build)
        self.assertIn("FACTORY_REMOVE_TRANSPORT_SIGNATURES=true", local_build)
        self.assertIn("FACTORY_REMOVE_TRANSPORT_SIGNATURES", build)
        self.assertIn("--remove-signatures", build)

    def test_local_ubi9_ca_is_activated_before_rpm_access(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        patch = (
            ROOT
            / "overlays"
            / "ubi9-minimal"
            / "patches"
            / "0002-update-ca-trust-before-rpm-access.patch"
        ).read_text(encoding="utf-8")

        self.assertIn("LOCAL_CA_CERT", local_build)
        self.assertIn("LOCAL_CA_BUNDLE", local_build)
        self.assertIn('curl_args+=(--cacert "${LOCAL_CA_BUNDLE}")', local_build)
        self.assertIn("local_ca_cert=${LOCAL_CA_CERT:-${LOCAL_CA_BUNDLE:-}}", local_build)
        self.assertIn("factory-local-ca.crt", local_build)
        self.assertLess(patch.index("+RUN update-ca-trust"), patch.index("microdnf repolist"))

    def test_internal_ubi_cache_uses_auth_and_honors_tls_override(self) -> None:
        repo_config = (ROOT / "scripts/write_repo_config.sh").read_text(encoding="utf-8")
        cache_config = repo_config.split("if [[ -n ${FACTORY_RPM_BASE_URL:-} ]]", maxsplit=1)[0]

        self.assertEqual(cache_config.count("sslverify=${FACTORY_RPM_SSLVERIFY:-1}"), 2)
        self.assertEqual(cache_config.count("password=${FACTORY_RPM_REPO_PASSWORD}"), 2)
        self.assertNotIn("cdn-ubi.redhat.com", cache_config)

    def test_ci_container_tools_are_rootless_and_use_vfs(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        build = (ROOT / "scripts/build_image.sh").read_text(encoding="utf-8")
        runner = (ROOT / "toolchain/Containerfile.factory-runner").read_text(encoding="utf-8")
        storage = (ROOT / "toolchain/storage.conf").read_text(encoding="utf-8")

        self.assertIn("'BUILDAH_ISOLATION=rootless'", jenkinsfile)
        self.assertIn("'STORAGE_DRIVER=vfs'", jenkinsfile)
        self.assertIn('--isolation "${isolation}"', build)
        self.assertNotIn("--isolation chroot", build)
        self.assertIn("USER ${FACTORY_UID}", runner)
        self.assertIn("/etc/subuid", runner)
        self.assertIn('driver = "vfs"', storage)

    def test_rootfs_scans_do_not_mount_container_storage(self) -> None:
        unpack = (ROOT / "scripts/unpack_image.sh").read_text(encoding="utf-8")
        scan = (ROOT / "scripts/scan_image.sh").read_text(encoding="utf-8")
        compliance = (ROOT / "scripts/compliance_scan.sh").read_text(encoding="utf-8")

        self.assertIn("podman unshare umoci unpack", unpack)
        self.assertIn("scripts/unpack_image.sh", scan)
        self.assertIn("scripts/unpack_image.sh", compliance)
        self.assertIn("podman unshare clamscan", scan)
        self.assertIn("podman unshare oscap-chroot", compliance)
        self.assertNotIn("podman mount", scan)
        self.assertNotIn("podman mount", compliance)

    def test_nested_runtimes_delegate_cgroups_to_kubernetes(self) -> None:
        local_build = (ROOT / "scripts/local_build.sh").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/assert_rpm_integrity.sh").read_text(encoding="utf-8")

        self.assertIn("--cgroups=disabled", local_build)
        self.assertIn("--cgroups=disabled", verifier)
        for profile in ("base", "bitbucket", "confluence", "jira"):
            test_script = (ROOT / "tests" / "profiles" / profile / "test.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("--cgroups=disabled", test_script)

    def test_rpm_snapshot_uses_workspace_local_dnf_state(self) -> None:
        snapshot = (ROOT / "scripts/snapshot_rpm_repo.sh").read_text(encoding="utf-8")

        self.assertIn("cachedir=${output}/dnf-cache", snapshot)
        self.assertIn("persistdir=${output}/dnf-persist", snapshot)
        self.assertIn("logdir=${output}/dnf-log", snapshot)

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
