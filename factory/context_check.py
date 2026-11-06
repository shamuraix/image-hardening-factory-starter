"""Static checks on a prepared build context (after overlays, before build).

Nothing here builds or runs the candidate image. The checks render the
product's own Jinja templates with its own ``entrypoint_helpers.py`` and
inspect source files, so they catch configuration defects the image would only
show at start-up. Expectations come from the catalog ``verify`` block.

The product's helper is executed in a child ``python3 -I`` process with a
minimal environment. That code comes from the signature-locked Iron Bank
source, and the stage that runs it holds no credentials.

Ported from Atlassian's docker-atlassian-* test suites, limited to features the
Iron Bank copies actually have:

* server.xml defaults and proxy parameters, dbconfig.xml per database type,
  cluster.properties, seraph, filestore (Jira); confluence.cfg.xml,
  confluence-init.properties, server.xml (Confluence);
* JDBC URLs and passwords containing ``&`` must still render valid XML
  (Atlassian's ``test_confluence_xml_postgres_ampersand``);
* ``unset_secure_vars`` keeps ``AWS_WEB_IDENTITY_TOKEN_FILE`` and the
  allowlist while removing secrets (Atlassian's ``test_*_secure_vars``);
* Bitbucket's git version against Atlassian's banned and unsupported list.
"""

from __future__ import annotations

import ast
import json
import py_compile
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Git releases Atlassian bans or does not support for Bitbucket
# (docker-atlassian-bitbucket-server bin/git-versions.py): 2.48 is banned for
# every version (BBSDEV-20059); 2.51-2.53 are unsupported and 2.54 has
# performance problems (BBSDEV-35481, BSERV-20473).
GIT_DENYLIST = {"2.48", "2.51", "2.52", "2.53", "2.54"}

# Jinja2 shipped by the base distribution (python3-jinja2 in the UBI
# AppStream repositories). Template results are compared under the same
# major.minor the image will use; see docs/configuration.md.
BASE_JINJA2 = {"9": "2.11", "10": "3.1"}

# Atlassian's DB password cipher settings: a family of variables sharing this
# prefix (shared-components is_secured_var; upstream tests use a suffixed name).
CIPHER_VAR = "com_atlassian_db_config_password_ciphers_algorithm_javax_crypto_foo_bar"

REQUIRED = "required"
ADVISORY = "advisory"

_RENDER_SCRIPT = r"""
import json, sys
spec = json.load(sys.stdin)
sys.path.insert(0, spec["helperDir"])
import jinja2
import entrypoint_helpers as helpers
helpers.jenv.loader = jinja2.FileSystemLoader(spec["templateDir"])
print(helpers.jenv.get_template(spec["template"]).render(**spec["context"]), end="")
"""

_UNSET_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import entrypoint_helpers as helpers
helpers.unset_secure_vars()
print(json.dumps(sorted(os.environ)))
"""


@dataclass
class Check:
    id: str
    severity: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    image: str
    checks: list[Check] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)

    def add(self, id_: str, severity: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(id_, severity, bool(passed), detail))

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == REQUIRED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "passed": self.passed,
            "environment": self.environment,
            "checks": [asdict(c) for c in self.checks],
        }


def dockerfile_env(dockerfile: str) -> dict[str, str]:
    """ENV defaults from a Dockerfile, with ${VAR} from earlier ARG/ENV values."""
    values: dict[str, str] = {}
    text = dockerfile.replace("\\\n", " ")
    for line in text.splitlines():
        match = re.match(r"^\s*(ARG|ENV)\s+(.*)$", line)
        if not match:
            continue
        body = match.group(2).strip()
        pairs = (
            re.findall(r'(\w+)=("[^"]*"|\S*)', body)
            if "=" in body
            else [tuple(body.split(None, 1))]
        )
        for pair in pairs:
            if len(pair) != 2:
                continue
            key, raw = pair
            raw = raw.strip('"')
            value = re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: values.get(m[1] or m[2], ""), raw)
            if match.group(1) == "ENV" or key not in values:
                values[key] = value
    return values


def _run_python(script: str, args: list[str], stdin: str | None, env: dict[str, str]) -> str:
    result = subprocess.run(
        # -B: importing the product helper must not write __pycache__ into the
        # sealed build context, which build reads in parallel.
        [sys.executable, "-I", "-B", "-c", script, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", **env},
        timeout=60,
        check=False,
        cwd=tempfile.gettempdir(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr else "failed")
    return result.stdout


def render(helper: Path, template_dir: Path, template: str, context: dict[str, str]) -> str:
    spec = {
        "helperDir": str(helper.parent),
        "templateDir": str(template_dir),
        "template": template,
        "context": context,
    }
    return _run_python(_RENDER_SCRIPT, [], json.dumps(spec), {})


# Scenario: (id, template, overrides, assertions). An assertion is
# ("xml", element-path, attribute-or-None, expected) or ("text", substring).
JIRA_DB = {
    "atl_jdbc_user": "jira",
    "atl_db_driver": "org.postgresql.Driver",
}
PROFILES: dict[str, list[tuple[str, str, dict[str, str], list[tuple]]]] = {
    "jira": [
        (
            "server-xml-defaults",
            "server.xml.j2",
            {},
            [("xml", ".//Connector", "port", "{port}"), ("xml", ".//Connector", "secure", "false")],
        ),
        (
            "server-xml-proxy-params",
            "server.xml.j2",
            {
                "atl_proxy_name": "jira.example.mil",
                "atl_proxy_port": "443",
                "atl_tomcat_scheme": "https",
                "atl_tomcat_secure": "true",
            },
            [
                ("xml", ".//Connector", "proxyName", "jira.example.mil"),
                ("xml", ".//Connector", "proxyPort", "443"),
                ("xml", ".//Connector", "scheme", "https"),
                ("xml", ".//Connector", "secure", "true"),
            ],
        ),
        (
            "dbconfig-postgres",
            "dbconfig.xml.j2",
            {**JIRA_DB, "atl_db_type": "postgres72", "atl_jdbc_url": "jdbc:postgresql://db/j"},
            [
                ("xml", "schema-name", None, "public"),
                ("xml", "database-type", None, "postgres72"),
                ("xml", "jdbc-datasource/url", None, "jdbc:postgresql://db/j"),
            ],
        ),
        (
            "dbconfig-mysql-schema",
            "dbconfig.xml.j2",
            {**JIRA_DB, "atl_db_type": "mysql8", "atl_jdbc_url": "jdbc:mysql://db/j"},
            [("xml", "schema-name", None, ""), ("xml", "database-type", None, "mysql8")],
        ),
        (
            "dbconfig-ampersands",
            "dbconfig.xml.j2",
            {
                **JIRA_DB,
                "atl_db_type": "postgres72",
                "atl_jdbc_url": "jdbc:postgresql://db/j?ssl=true&sslmode=require",
                "atl_jdbc_password": "p&ss",
            },
            [
                (
                    "xml",
                    "jdbc-datasource/url",
                    None,
                    "jdbc:postgresql://db/j?ssl=true&sslmode=require",
                ),
                ("xml", "jdbc-datasource/password", None, "p&ss"),
            ],
        ),
        (
            "dbconfig-mixed-escaping-and-markup",
            "dbconfig.xml.j2",
            {
                **JIRA_DB,
                "atl_db_type": "postgres72",
                "atl_jdbc_url": "jdbc:x://db/j?a=1&amp;b=2&c=3",
                "atl_jdbc_password": "p<w>&amp;&\"'",
            },
            [
                ("xml", "jdbc-datasource/url", None, "jdbc:x://db/j?a=1&b=2&c=3"),
                ("xml", "jdbc-datasource/password", None, "p<w>&&\"'"),
            ],
        ),
        (
            "dbconfig-ampersands-pre-escaped",
            "dbconfig.xml.j2",
            {**JIRA_DB, "atl_db_type": "postgres72", "atl_jdbc_url": "jdbc:x://db/j?a=1&amp;b=2"},
            [("xml", "jdbc-datasource/url", None, "jdbc:x://db/j?a=1&b=2")],
        ),
        (
            "cluster-properties",
            "cluster.properties.j2",
            {
                "jira_node_id": "node-1",
                "jira_shared_home": "/shared",
                "ehcache_listener_port": "40001",
            },
            [
                ("text", "jira.node.id=node-1"),
                ("text", "jira.shared.home=/shared"),
                ("text", "ehcache.listener.port=40001"),
            ],
        ),
        ("seraph-defaults", "seraph-config.xml.j2", {}, [("text", "1209600")]),
        (
            "filestore-attachments-s3",
            "filestore-config.xml.j2",
            {
                "atl_s3attachments_bucket_name": "attachments",
                "atl_s3attachments_region": "us-gov-west-1",
            },
            [("text", "<bucket-name>attachments</bucket-name>"), ("text", "us-gov-west-1")],
        ),
    ],
    "confluence": [
        (
            "server-xml-defaults",
            "server.xml.j2",
            {},
            [("xml", ".//Connector", "port", "{port}"), ("xml", ".//Connector", "secure", "false")],
        ),
        (
            "server-xml-proxy-params",
            "server.xml.j2",
            {
                "atl_proxy_name": "wiki.example.mil",
                "atl_proxy_port": "443",
                "atl_tomcat_scheme": "https",
            },
            [
                ("xml", ".//Connector", "proxyName", "wiki.example.mil"),
                ("xml", ".//Connector", "proxyPort", "443"),
                ("xml", ".//Connector", "scheme", "https"),
            ],
        ),
        (
            "confluence-cfg-postgres-ampersands",
            "confluence.cfg.xml.j2",
            {
                "atl_db_type": "postgresql",
                "atl_jdbc_url": "jdbc:postgresql://db/c?ssl=true&sslmode=require",
                "atl_jdbc_user": "confluence",
                "atl_jdbc_password": "p&ss",
            },
            [
                (
                    "xml",
                    ".//property[@name='hibernate.connection.url']",
                    None,
                    "jdbc:postgresql://db/c?ssl=true&sslmode=require",
                ),
                ("xml", ".//property[@name='hibernate.connection.password']", None, "p&ss"),
                ("xml", ".//property[@name='confluence.database.choice']", None, "postgresql"),
            ],
        ),
        (
            "confluence-cfg-mixed-escaping-and-markup",
            "confluence.cfg.xml.j2",
            {
                "atl_db_type": "postgresql",
                "atl_jdbc_url": "jdbc:x://db/c?a=1&amp;b=2&c=3",
                "atl_jdbc_user": "confluence",
                "atl_jdbc_password": "p<w>&amp;&",
            },
            [
                (
                    "xml",
                    ".//property[@name='hibernate.connection.url']",
                    None,
                    "jdbc:x://db/c?a=1&b=2&c=3",
                ),
                ("xml", ".//property[@name='hibernate.connection.password']", None, "p<w>&&"),
            ],
        ),
        ("confluence-cfg-defaults", "confluence.cfg.xml.j2", {}, []),
        (
            "confluence-init-home",
            "confluence-init.properties.j2",
            {},
            [("text", "confluence.home = {home}")],
        ),
        ("seraph-defaults", "seraph-config.xml.j2", {"atl_autologin_cookie_age": "1209600"}, []),
    ],
    "bitbucket": [],
}


def _assert(rendered: str, assertion: tuple, values: dict[str, str]) -> str | None:
    kind = assertion[0]
    if kind == "text":
        expected = assertion[1].format(**values)
        return None if expected in rendered else f"missing {expected!r}"
    _, path, attribute, expected = assertion
    expected = expected.format(**values)
    root = ET.fromstring(rendered.encode())
    node = root.find(path)
    if node is None:
        return f"no element {path}"
    actual = node.get(attribute) if attribute else (node.text or "").strip()
    return (
        None if actual == expected else f"{path}{'@' + attribute if attribute else ''}={actual!r}"
    )


def _env_cleanup(entrypoint: str) -> bool:
    """True if the entrypoint asks exec_app to remove sensitive variables."""
    try:
        tree = ast.parse(entrypoint)
    except SyntaxError:
        return False  # entrypoint-compiles already reports this as a required failure
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "exec_app":
            for keyword in node.keywords:
                if keyword.arg == "env_cleanup":
                    value = keyword.value
                    return not (isinstance(value, ast.Constant) and value.value is False)
    return False


def check_context(catalog_path: Path, context: Path) -> Report:
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    report = Report(catalog["metadata"]["name"])
    report.environment = {
        "python": ".".join(map(str, sys.version_info[:2])),
        "jinja2": _jinja2_version(),
    }
    verify = catalog.get("verify")
    if not verify:
        report.add("profile", ADVISORY, True, "no verify block; nothing to check")
        return report

    base = _base_major(catalog_path, catalog)
    expected_jinja = BASE_JINJA2.get(base, "")
    report.environment.update({"baseMajor": base, "baseJinja2": expected_jinja})
    report.add(
        "jinja2-parity",
        ADVISORY,
        report.environment["jinja2"].startswith(expected_jinja + "."),
        f"rendered with Jinja2 {report.environment['jinja2']}; UBI {base} ships {expected_jinja}.x",
    )

    sources = verify["sources"]
    helper = context / sources["helpers"]
    entrypoint = context / sources["entrypoint"]
    for label, path in (("entrypoint", entrypoint), ("helpers", helper)):
        try:
            py_compile.compile(
                str(path), cfile=str(Path(tempfile.mkdtemp()) / "x.pyc"), doraise=True
            )
            report.add(f"{label}-compiles", REQUIRED, True, path.name)
        except (py_compile.PyCompileError, OSError) as error:
            report.add(f"{label}-compiles", REQUIRED, False, str(error).splitlines()[-1])

    values = dockerfile_env((context / catalog["source"]["containerfile"]).read_text("utf-8"))
    context_env = {key.lower(): value for key, value in values.items()}
    fmt = {"port": str(verify["ports"][0]), "home": values.get(verify["homeEnv"], "")}
    template_dir = context / sources.get("templates", ".")
    for scenario, template, overrides, assertions in PROFILES[verify["profile"]]:
        check_id = f"template:{template}:{scenario}"
        try:
            rendered = render(helper, template_dir, template, {**context_env, **overrides})
            if template.endswith(".xml.j2"):
                ET.fromstring(rendered.encode())
            failures = [f for f in (_assert(rendered, a, fmt) for a in assertions) if f]
            report.add(check_id, REQUIRED, not failures, "; ".join(failures) or "ok")
        except (ET.ParseError, RuntimeError, KeyError) as error:
            report.add(check_id, REQUIRED, False, f"{type(error).__name__}: {error}")

    if _env_cleanup(entrypoint.read_text("utf-8")):
        try:
            remaining = set(
                json.loads(
                    _run_python(
                        _UNSET_SCRIPT,
                        [str(helper.parent)],
                        None,
                        {
                            "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/eks/token",
                            "ATL_JDBC_PASSWORD": "secret",
                            "MY_TOKEN": "keep-me",
                            "ATL_ALLOWLIST_SENSITIVE_ENV_VARS": "MY_TOKEN",
                            # Allowlist entries are exact names, not prefixes.
                            "MY_TOKEN_SECRET": "remove-me",
                            "AWS_WEB_IDENTITY_TOKEN_FILE_SECRET": "remove-me",
                            # Atlassian's cipher settings are a prefix family.
                            CIPHER_VAR: "/path/to/file",
                        },
                    )
                )
            )
            problems = []
            if "AWS_WEB_IDENTITY_TOKEN_FILE" not in remaining:
                problems.append("removes AWS_WEB_IDENTITY_TOKEN_FILE (breaks EKS IRSA)")
            if "MY_TOKEN" not in remaining:
                problems.append("ignores ATL_ALLOWLIST_SENSITIVE_ENV_VARS")
            if "ATL_JDBC_PASSWORD" in remaining:
                problems.append("keeps ATL_JDBC_PASSWORD")
            for suffixed in ("MY_TOKEN_SECRET", "AWS_WEB_IDENTITY_TOKEN_FILE_SECRET"):
                if suffixed in remaining:
                    problems.append(f"keeps {suffixed} (allowlist matched as a prefix)")
            if CIPHER_VAR not in remaining:
                problems.append("removes the Atlassian password-cipher settings")
            report.add("unset-secure-vars", REQUIRED, not problems, "; ".join(problems) or "ok")
        except RuntimeError as error:
            report.add("unset-secure-vars", REQUIRED, False, str(error))
    else:
        report.add("unset-secure-vars", ADVISORY, True, "entrypoint does not request env cleanup")

    if verify["profile"] == "bitbucket":
        manifest = yaml.safe_load(
            (context / catalog["source"]["hardeningManifest"]).read_text("utf-8")
        )
        versions = [
            m.group(1)
            for r in manifest.get("resources", [])
            if (m := re.match(r"git-(\d+\.\d+)\.\d+\.tar\.gz$", str(r.get("filename", ""))))
        ]
        banned = sorted(set(versions) & GIT_DENYLIST)
        report.add(
            "git-version",
            REQUIRED,
            bool(versions) and not banned,
            f"git {', '.join(versions) or 'not found in hardening manifest'}"
            + (f"; banned/unsupported by Atlassian: {', '.join(banned)}" if banned else ""),
        )
    return report


def _jinja2_version() -> str:
    try:
        import jinja2

        return jinja2.__version__
    except ImportError:
        return "missing"


def _base_major(catalog_path: Path, catalog: dict[str, Any]) -> str:
    base = catalog["build"]["base"]
    if base["kind"] == "catalog":
        base_catalog = yaml.safe_load((catalog_path.parent / f"{base['image']}.yaml").read_text())
        return str(base_catalog["product"]["version"]).split(".")[0]
    return str(catalog["product"]["version"]).split(".")[0]


def write_report(report: Report, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
