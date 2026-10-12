"""context-check renders the product's own templates and helpers without running the image.

Full Jira/Confluence template scenarios need the Iron Bank sources, which are
not vendored here; these tests cover the mechanics with fixture contexts. The
PR that added the checks ran them against the pinned Iron Bank revisions.
"""

import shutil
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from factory.context_check import GIT_DENYLIST, PROFILES, check_context, dockerfile_env, render

ROOT = Path(__file__).resolve().parents[2]

HELPER_HEAD = """\
import os, re, logging
import jinja2 as j2
env = {k.lower(): v for k, v in os.environ.items()}
jenv = j2.Environment(loader=j2.FileSystemLoader('/opt/atlassian/etc/'),
                      autoescape=j2.select_autoescape(['xml']))
"""
# Iron Bank's 2022 copy: no allowlist, so the IRSA token path is removed.
HELPER_OLD = (
    HELPER_HEAD
    + """
def unset_secure_vars():
    for key in os.environ:
        if any(kw in key.upper() for kw in ('PASS', 'SECRET', 'TOKEN')):
            del os.environ[key]
"""
)
HELPER_FIXED = (
    HELPER_HEAD
    + """
def unset_secure_vars():
    allow = {v.strip().upper() for v in os.environ.get('ATL_ALLOWLIST_SENSITIVE_ENV_VARS', '').split(',') if v.strip()}
    allow.add('AWS_WEB_IDENTITY_TOKEN_FILE')
    for key in list(os.environ):
        if (any(kw in key.upper() for kw in ('PASS', 'SECRET', 'TOKEN'))
                and key.upper() not in allow
                and not key.lower().startswith('com_atlassian_db_config_password_ciphers_algorithm_javax_crypto')):
            del os.environ[key]
"""
)
# Upstream's prefix match: an allowlisted name also protects longer names.
HELPER_PREFIX = (
    HELPER_HEAD
    + """
def unset_secure_vars():
    allow = [v.strip() for v in os.environ.get('ATL_ALLOWLIST_SENSITIVE_ENV_VARS', '').split(',') if v.strip()]
    pattern = '^(?:' + '|'.join(['AWS_WEB_IDENTITY_TOKEN_FILE', 'com_atlassian_db_config_password_ciphers_algorithm_javax_crypto'] + [re.escape(v) for v in allow]) + ')'
    for key in list(os.environ):
        if any(kw in key.upper() for kw in ('PASS', 'SECRET', 'TOKEN')) and not re.match(pattern, key, re.I):
            del os.environ[key]
"""
)


def make_context(
    root: Path, helper: str, git: str = "2.49.1", cleanup: bool = True
) -> tuple[Path, Path]:
    catalogs = root / "catalog"
    catalogs.mkdir()
    shutil.copy(ROOT / "catalog/images/ubi10-minimal.yaml", catalogs)
    catalog = yaml.safe_load((ROOT / "catalog/images/bitbucket-lts.yaml").read_text())
    (catalogs / "bitbucket-lts.yaml").write_text(yaml.safe_dump(catalog))
    context = root / "context"
    (context / "scripts/shared-components/image").mkdir(parents=True)
    (context / "scripts/shared-components/image/entrypoint_helpers.py").write_text(helper)
    flag = "True" if cleanup else "False"
    (context / "scripts/entrypoint.py").write_text(
        f"from entrypoint_helpers import exec_app\nexec_app(['x'], '/h', name='B', env_cleanup={flag})\n"
    )
    (context / "Dockerfile").write_text(
        "ARG BASE_REF\nFROM ${BASE_REF}\nARG BITBUCKET_VERSION=10.2.7\n"
        "ENV RUN_USER=bitbucket\nENV BITBUCKET_HOME=/var/atlassian/application-data/bitbucket\n"
    )
    (context / "hardening_manifest.yaml").write_text(
        yaml.safe_dump({"tags": ["10.2.7"], "resources": [{"filename": f"git-{git}.tar.gz"}]})
    )
    return catalogs / "bitbucket-lts.yaml", context


def results(report):
    return {check.id: check for check in report.checks}


class ContextCheckTests(unittest.TestCase):
    def test_irsa_token_and_allowlist_are_enforced(self):
        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_OLD)
            report = check_context(catalog, context)
        self.assertFalse(report.passed)
        self.assertIn("AWS_WEB_IDENTITY_TOKEN_FILE", results(report)["unset-secure-vars"].detail)

        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_FIXED)
            report = check_context(catalog, context)
        self.assertTrue(report.passed, report.to_dict())

    def test_allowlist_is_exact_not_prefix(self):
        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_PREFIX)
            detail = results(check_context(catalog, context))["unset-secure-vars"].detail
        self.assertIn("MY_TOKEN_SECRET", detail)
        self.assertIn("AWS_WEB_IDENTITY_TOKEN_FILE_SECRET", detail)

    def test_helper_execution_leaves_no_bytecode_in_the_context(self):
        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_FIXED)
            check_context(catalog, context)
            self.assertEqual(list(context.rglob("__pycache__")), [])
            self.assertEqual(list(context.rglob("*.pyc")), [])

    def test_irsa_check_only_applies_when_entrypoint_requests_cleanup(self):
        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_OLD, cleanup=False)
            report = check_context(catalog, context)
        self.assertTrue(report.passed)
        self.assertEqual(results(report)["unset-secure-vars"].severity, "advisory")

    def test_banned_git_versions_fail(self):
        self.assertTrue({"2.48", "2.52"} <= GIT_DENYLIST)
        for version, ok in (("2.49.1", True), ("2.48.0", False), ("2.52.0", False)):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                catalog, context = make_context(Path(directory), HELPER_FIXED, git=version)
                self.assertEqual(results(check_context(catalog, context))["git-version"].passed, ok)

    def test_syntax_error_in_entrypoint_fails(self):
        with TemporaryDirectory() as directory:
            catalog, context = make_context(Path(directory), HELPER_FIXED, cleanup=False)
            (context / "scripts/entrypoint.py").write_text("def broken(:\n")
            self.assertFalse(results(check_context(catalog, context))["entrypoint-compiles"].passed)

    def test_render_uses_the_products_autoescape_setting(self):
        # Iron Bank's select_autoescape(['xml']) never matches "*.xml.j2", so a raw
        # "&" reaches the XML; this is why the overlays escape JDBC values.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "entrypoint_helpers.py").write_text(HELPER_FIXED)
            (root / "t.xml.j2").write_text("<a>{{ v }}</a>")
            self.assertEqual(
                render(root / "entrypoint_helpers.py", root, "t.xml.j2", {"v": "x&y"}), "<a>x&y</a>"
            )

    def test_dockerfile_env_resolves_arg_defaults(self):
        values = dockerfile_env(
            textwrap.dedent(
                """\
                ARG CONFLUENCE_VERSION=10.2.18
                ENV CONFLUENCE_VERSION=${CONFLUENCE_VERSION}
                ENV JRE_HOME=/usr/lib/jvm/jre-21-openjdk
                ENV JAVA_BINARY=${JRE_HOME}/bin/java
                """
            )
        )
        self.assertEqual(values["CONFLUENCE_VERSION"], "10.2.18")
        self.assertEqual(values["JAVA_BINARY"], "/usr/lib/jvm/jre-21-openjdk/bin/java")

    def test_every_app_profile_declares_scenarios_and_catalog_verify(self):
        for image, profile in (
            ("jira-lts", "jira"),
            ("confluence-lts", "confluence"),
            ("bitbucket-lts", "bitbucket"),
        ):
            catalog = yaml.safe_load((ROOT / f"catalog/images/{image}.yaml").read_text())
            self.assertEqual(catalog["verify"]["profile"], profile)
            self.assertEqual(catalog["build"]["base"]["image"], "ubi10-minimal")
        self.assertTrue(PROFILES["jira"] and PROFILES["confluence"])
        ampersand = [s for p in ("jira", "confluence") for s in PROFILES[p] if "ampersand" in s[0]]
        self.assertEqual(len(ampersand), 3)


if __name__ == "__main__":
    unittest.main()
