---
name: atlassian-lts
description: Facts about the Bitbucket, Jira (with JSM), and Confluence Data Center LTS images this factory hardens — catalog fields, build args, test expectations, and how vulnerabilities in bundled libraries are remediated. Use when upgrading a product version or interpreting findings in /opt.
---

# Atlassian Data Center LTS images

| Image | Iron Bank repository | Build args | Test profile |
|---|---|---|---|
| `bitbucket-lts` | `dsop/atlassian/bitbucket-data-center/bitbucket-lts` | `BITBUCKET_VERSION` | `tests/profiles/bitbucket` |
| `jira-lts` | `dsop/atlassian/jira-data-center/jira-lts` | `JIRA_VERSION`, `JSM_VERSION` (kept equal) | `tests/profiles/jira` |
| `confluence-lts` | `dsop/atlassian/confluence-data-center/confluence-lts` | `CONFLUENCE_VERSION` | `tests/profiles/confluence` |

All three build on the catalog base `ubi9-minimal` (`build.base.kind: catalog`),
publish under `atlassian/<image>`, and use policy profile
`atlassian-rhel9-container` (STIG datastream for RHEL 9).

## Version changes

A product upgrade touches, together:

1. `catalog/images/<image>.yaml` — `product.version` and every `build.buildArgs` value.
2. The Dockerfile `ARG <PRODUCT>_VERSION=` lines (via the overlay or upstream).
3. `hardening_manifest.yaml` — `tags[0]` and the product tarball resource
   (`url`, `filename`, checksum). New tarballs need intake to re-lock.
4. Possibly the test profile, if ports, users, UIDs, or install paths changed.

Stay on an Atlassian **LTS** line unless a human explicitly decides otherwise.
Never downgrade.

## Test profile expectations (examples from `tests/profiles/jira/test.sh`)

- Runs as user `jira`, UID 2001; exposes `8080/tcp` and `40001/tcp`.
- `/usr/bin/tini` and `/entrypoint.py` exist; Java runs.
- JSM `.obr` installers are removed after install; plugins are present.
- `scripts/assert_rpm_integrity.sh` checks `rpm -V` against the allow lists.

## Vulnerabilities in bundled libraries

Findings under `/opt/<product>/...` are libraries Atlassian ships inside the
product (`inApplicationArchive: true`). They are remediated **only** by upgrading
the whole product to an LTS release that bundles the fixed library. Replacing
individual JARs breaks vendor support and is not acceptable. If no such release
exists, the finding is blocked pending the vendor; humans decide on exceptions.
