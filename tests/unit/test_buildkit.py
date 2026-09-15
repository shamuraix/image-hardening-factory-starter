from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from factory.buildkit import DockerfileAdaptationError, adapt_dockerfile_text

ROOT = Path(__file__).resolve().parents[2]


class DockerfileAdaptationTests(unittest.TestCase):
    def test_injects_repo_mounts_and_strips_external_frontend(self) -> None:
        adapted = adapt_dockerfile_text(
            textwrap.dedent(
                """\
                # syntax=docker/dockerfile:1.7
                FROM ${BASE_REF} AS base
                RUN microdnf -y update && \\
                    microdnf clean all
                RUN ["microdnf", "-y", "install", "tar"]
                RUN <<EOF
                echo heredoc
                EOF
                """
            )
        )

        self.assertNotIn("syntax=docker/dockerfile", adapted)
        self.assertEqual(adapted.count("--mount=type=tmpfs,target=/etc/yum.repos.d"), 3)
        self.assertEqual(adapted.count("id=factory-repo"), 3)
        self.assertIn("RUN --mount=type=tmpfs,target=/etc/yum.repos.d ", adapted)
        self.assertIn("RUN --mount=type=tmpfs,target=/etc/yum.repos.d ", adapted)
        self.assertIn("<<EOF\necho heredoc\nEOF\n", adapted)

    def test_rejects_external_sources(self) -> None:
        cases = [
            "FROM registry.access.redhat.com/ubi9-minimal:9.8\n",
            "FROM ${BASE_REF} AS base\nFROM alpine:3.20\n",
            "FROM ${BASE_REF}\nCOPY --from=docker.io/library/busybox:latest /bin/sh /bin/sh\n",
            "FROM ${BASE_REF}\nADD https://example.invalid/file /file\n",
            'FROM ${BASE_REF}\nADD ["https://example.invalid/file", "/file"]\n',
        ]
        for dockerfile in cases:
            with self.subTest(dockerfile=dockerfile):
                with self.assertRaises(DockerfileAdaptationError):
                    adapt_dockerfile_text(dockerfile)

    def test_allows_local_multistage_copy(self) -> None:
        adapted = adapt_dockerfile_text(
            textwrap.dedent(
                """\
                FROM ${BASE_REF} AS builder
                RUN echo ok > /artifact
                FROM builder
                COPY --from=builder /artifact /artifact
                """
            )
        )

        self.assertIn("FROM builder", adapted)
        self.assertIn("COPY --from=builder", adapted)

    def test_preserves_comments_and_blank_lines_inside_continuations(self) -> None:
        adapted = adapt_dockerfile_text(
            textwrap.dedent(
                """\
                FROM ${BASE_REF}
                RUN echo first && \\
                # preserved comment

                    echo second
                LABEL name=value
                """
            )
        )

        self.assertIn("# preserved comment\n\n    echo second\n", adapted)
        self.assertIn("LABEL name=value\n", adapted)
        self.assertEqual(adapted.count("--mount=type=tmpfs,target=/etc/yum.repos.d"), 1)

    def test_rejects_reserved_repo_mounts(self) -> None:
        with self.assertRaises(DockerfileAdaptationError):
            adapt_dockerfile_text(
                "FROM ${BASE_REF}\nRUN --mount=type=secret,id=factory-repo,target=/x echo unsafe\n"
            )


class BuildImageBuildKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="buildkit-")
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.bin = self.project / "bin"
        self.bin.mkdir()
        (self.project / "scripts").mkdir()
        (self.project / "catalog/images").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/build_image.sh", self.project / "scripts/build_image.sh")
        os.chmod(self.project / "scripts/build_image.sh", 0o755)
        self._write_helpers()
        self.work = self.project / "work/test"
        (self.work / "context").mkdir(parents=True)
        (self.work / "context/Dockerfile").write_text(
            "FROM ${BASE_REF}\nRUN microdnf -y update\n", encoding="utf-8"
        )
        (self.work / "base.oci.tar").write_text("base", encoding="utf-8")
        (self.work / "resource-lock.json").write_text(
            json.dumps({"localDevelopment": True}), encoding="utf-8"
        )
        (self.work / "build.env").write_text(
            "\n".join(
                [
                    "FACTORY_IMAGE=test",
                    "SOURCE_REVISION=abc123",
                    "BASE_REF=localhost/factory/base:local",
                    "BASE_DIGEST=sha256:base",
                    "RPM_REPOMD_DIGEST=sha256:repomd",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.project / "catalog/images/test.yaml").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_executable(self, relative: str, content: str) -> None:
        path = self.project / relative
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_helpers(self) -> None:
        self._write_executable(
            "scripts/require_rootless.sh",
            """\
            #!/usr/bin/env bash
            exit 0
            """,
        )
        self._write_executable(
            "scripts/write_repo_config.sh",
            """\
            #!/usr/bin/env bash
            cat >"$2" <<'EOF'
            [factory]
            name=factory
            baseurl=https://artifactory.invalid/rpm
            EOF
            printf 'snapshot-1\\n'
            """,
        )
        self._write_executable(
            "scripts/run_buildkit.sh",
            """\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" >"${RUN_BUILDKIT_LOG:?}"
            dockerfile_dir=
            previous=
            for arg in "$@"; do
              if [[ ${previous} == --source-policy-file && -n ${SOURCE_POLICY_CAPTURE:-} ]]; then
                cp "${arg}" "${SOURCE_POLICY_CAPTURE}"
              fi
              case "$arg" in
                dockerfile=*) dockerfile_dir="${arg#dockerfile=}" ;;
                type=oci,dest=*)
                  dest="${arg#type=oci,dest=}"
                  dest="${dest%%,*}"
                  mkdir -p "$(dirname "$dest")"
                  printf 'buildkit archive' >"$dest"
                  ;;
              esac
              previous="${arg}"
            done
            if [[ -n ${dockerfile_dir} && -n ${ADAPTED_DOCKERFILE_CAPTURE:-} ]]; then
              cp "${dockerfile_dir}/Dockerfile" "${ADAPTED_DOCKERFILE_CAPTURE}"
              if [[ -f "${dockerfile_dir}/Dockerfile.dockerignore" ]]; then
                cp "${dockerfile_dir}/Dockerfile.dockerignore" "${DOCKERIGNORE_CAPTURE:?}"
              fi
            fi
            """,
        )
        self._write_executable(
            "bin/yq",
            """\
            #!/usr/bin/env python3
            from __future__ import annotations
            import os, sys
            query = sys.argv[2]
            path = sys.argv[3] if len(sys.argv) > 3 else ""
            if query == ".source.containerfile":
                print("Dockerfile")
            elif query == ".build.platforms[0]":
                print("linux/amd64")
            elif query == ".build.base.kind":
                print("upstream")
            elif query == ".product.version | split(\\".\\")[0]":
                print("9")
            elif query == ".build.base.image":
                print("ubi9-minimal")
            elif query == ".build.buildArgs // {} | to_entries[] | [.key,.value] | @tsv":
                print(os.environ.get("YQ_BUILD_ARGS", "APP_VERSION\\t1.2.3"))
            else:
                raise SystemExit(f"unexpected yq query {query!r} for {path}")
            """,
        )
        self._write_executable(
            "bin/jq",
            """\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["-e", ".localDevelopment == true"]:
                data = json.load(open(args[2], encoding="utf-8"))
                raise SystemExit(0 if data.get("localDevelopment") is True else 1)
            if args[:2] == ["-er", ".Digest"]:
                print(json.load(sys.stdin)["Digest"])
                raise SystemExit(0)
            if args and args[0] == "-n":
                out = {}
                index = 1
                while index < len(args):
                    if args[index] == "--arg":
                        out[args[index + 1]] = args[index + 2]
                        index += 3
                    else:
                        index += 1
                print(json.dumps(out, sort_keys=True))
                raise SystemExit(0)
            raise SystemExit(f"unexpected jq args {args!r}")
            """,
        )
        self._write_executable(
            "bin/git",
            """\
            #!/usr/bin/env bash
            printf '2024-01-02T03:04:05+00:00\\n'
            """,
        )
        self._write_executable(
            "bin/skopeo",
            """\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json, os, pathlib, sys
            log = os.environ["SKOPEO_LOG"]
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("\\t".join(sys.argv[1:]) + "\\n")
            if sys.argv[1] == "inspect":
                target = sys.argv[2]
                digest = os.environ.get("BASE_OBSERVED_DIGEST", "sha256:base")
                if "image.oci.tar" in target:
                    digest = os.environ.get("FINAL_DIGEST", "sha256:final")
                print(json.dumps({"Digest": digest}))
                raise SystemExit(0)
            if sys.argv[1] == "login":
                sys.stdin.read()
                raise SystemExit(0)
            if sys.argv[1] == "copy":
                dest = sys.argv[-1]
                if dest.startswith("oci-archive:"):
                    value = dest.removeprefix("oci-archive:")
                    archive = value.split(".oci.tar", 1)[0] + ".oci.tar"
                    pathlib.Path(archive).parent.mkdir(parents=True, exist_ok=True)
                    pathlib.Path(archive).write_text("archive", encoding="utf-8")
                elif dest.startswith("oci:"):
                    value = dest.removeprefix("oci:")
                    directory = value.rsplit(":", 1)[0]
                    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
                raise SystemExit(0)
            raise SystemExit(f"unexpected skopeo args {sys.argv[1:]!r}")
            """,
        )

    def _run_build(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "PYTHONPATH": str(ROOT),
                "FACTORY_PRIVATE_TMPDIR": str(self.project / "private"),
                "RUN_BUILDKIT_LOG": str(self.project / "run-buildkit.log"),
                "SKOPEO_LOG": str(self.project / "skopeo.log"),
                "ADAPTED_DOCKERFILE_CAPTURE": str(self.project / "adapted.Dockerfile"),
                "DOCKERIGNORE_CAPTURE": str(self.project / "adapted.Dockerfile.dockerignore"),
                "SOURCE_POLICY_CAPTURE": str(self.project / "source-policy.json"),
                "TMPDIR": str(self.project / "private"),
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["scripts/build_image.sh", "catalog/images/test.yaml", "work/test"],
            cwd=self.project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_build_image_uses_offline_oci_base_and_normalizes_output(self) -> None:
        result = self._run_build()

        self.assertEqual(result.returncode, 0, result.stderr)
        run_args = (self.project / "run-buildkit.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(run_args[0], "build")
        self.assertIn("--frontend", run_args)
        self.assertIn("dockerfile.v0", run_args)
        self.assertIn("--no-cache", run_args)
        self.assertIn("context:factory-base=oci-layout://base@sha256:base", run_args)
        self.assertIn("build-arg:BASE_REF=factory-base", run_args)
        self.assertIn("build-arg:BASE_MAJOR=9", run_args)
        self.assertIn("build-arg:SOURCE_DATE_EPOCH=1704164645", run_args)
        self.assertIn("build-arg:APP_VERSION=1.2.3", run_args)
        self.assertIn("force-network-mode=default", run_args)
        self.assertIn("--source-policy-file", run_args)
        source_policy = self.project / "source-policy.json"
        source_rules = json.loads(source_policy.read_text(encoding="utf-8"))["rules"]
        self.assertIn(
            {"action": "DENY", "selector": {"identifier": "docker-image://*"}}, source_rules
        )
        self.assertNotIn("network.host", run_args)
        self.assertIn("id=factory-repo,src=", "\n".join(run_args))
        self.assertIn("rewrite-timestamp=true", "\n".join(run_args))
        dockerfile_dir = next(
            arg.removeprefix("dockerfile=") for arg in run_args if arg.startswith("dockerfile=")
        )
        repo_secret = next(
            arg.removeprefix("id=factory-repo,src=")
            for arg in run_args
            if arg.startswith("id=factory-repo,src=")
        )
        self.assertNotEqual(Path(dockerfile_dir), Path(repo_secret).parent)

        adapted = (self.project / "adapted.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("--mount=type=tmpfs,target=/etc/yum.repos.d", adapted)
        self.assertIn("--mount=type=secret", adapted)
        self.assertIn("id=factory-repo", adapted)
        self.assertNotIn("baseurl=https://artifactory.invalid", adapted)

        skopeo_log = (self.project / "skopeo.log").read_text(encoding="utf-8")
        self.assertIn("copy\t--all\t--preserve-digests", skopeo_log)
        self.assertIn("oci-archive:work/test/base.oci.tar\toci:", skopeo_log)
        self.assertIn(
            "oci-archive:work/test/image.oci.tar:localhost/factory/test:local", skopeo_log
        )
        self.assertNotIn("docker://", skopeo_log)
        self.assertFalse((self.project / "private").exists())

        metadata = json.loads((self.work / "image-metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["digest"], "sha256:final")
        build_env = (self.work / "build.env").read_text(encoding="utf-8")
        self.assertIn("IMAGE_DIGEST=sha256:final", build_env)
        self.assertIn("LOCAL_IMAGE_REF=localhost/factory/test:local", build_env)

    def test_build_image_requests_host_network_only_when_explicit(self) -> None:
        result = self._run_build({"FACTORY_BUILD_NETWORK": "host"})

        self.assertEqual(result.returncode, 0, result.stderr)
        run_args = (self.project / "run-buildkit.log").read_text(encoding="utf-8").splitlines()
        self.assertIn("force-network-mode=host", run_args)
        self.assertIn("--allow", run_args)
        self.assertIn("network.host", run_args)

    def test_build_image_copies_original_containerfile_dockerignore(self) -> None:
        (self.work / "context/Dockerfile.dockerignore").write_text(
            "ignored-by-generated-dockerfile\n", encoding="utf-8"
        )
        (self.work / "context/.dockerignore").write_text(
            "normal-context-ignore\n", encoding="utf-8"
        )
        result = self._run_build()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.project / "adapted.Dockerfile.dockerignore").read_text(encoding="utf-8"),
            "ignored-by-generated-dockerfile\n",
        )

    def test_build_image_rejects_base_digest_mismatch_before_build(self) -> None:
        result = self._run_build({"BASE_OBSERVED_DIGEST": "sha256:other"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base archive digest does not match", result.stderr)
        self.assertFalse((self.project / "run-buildkit.log").exists())
        self.assertFalse((self.project / "private").exists())

    def test_build_image_rejects_reserved_catalog_build_args(self) -> None:
        result = self._run_build({"YQ_BUILD_ARGS": "BASE_REF\tunsafe"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("catalog build arg BASE_REF is reserved", result.stderr)
        self.assertFalse((self.project / "run-buildkit.log").exists())

    def test_build_image_rejects_buildkit_syntax_build_arg(self) -> None:
        result = self._run_build({"YQ_BUILD_ARGS": "BUILDKIT_SYNTAX\tdocker/dockerfile:1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("catalog build arg BUILDKIT_SYNTAX is reserved", result.stderr)
        self.assertFalse((self.project / "run-buildkit.log").exists())

    def test_build_image_acquires_missing_base_with_private_auth_cleanup(self) -> None:
        (self.work / "base.oci.tar").unlink()
        (self.work / "build.env").write_text(
            "\n".join(
                [
                    "FACTORY_IMAGE=test",
                    "SOURCE_REVISION=abc123",
                    "BASE_REF=registry.internal.example/factory/base@sha256:base",
                    "BASE_DIGEST=sha256:base",
                    "RPM_REPOMD_DIGEST=sha256:repomd",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = self._run_build(
            {
                "ARTIFACTORY_REGISTRY": "registry.internal.example",
                "ARTIFACTORY_READ_TOKEN": "token-for-test",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        skopeo_log = (self.project / "skopeo.log").read_text(encoding="utf-8")
        self.assertIn("login\t--authfile\t", skopeo_log)
        self.assertIn("copy\t--authfile\t", skopeo_log)
        self.assertIn("docker://registry.internal.example/factory/base@sha256:base", skopeo_log)
        self.assertFalse((self.project / "private").exists())


if __name__ == "__main__":
    unittest.main()
