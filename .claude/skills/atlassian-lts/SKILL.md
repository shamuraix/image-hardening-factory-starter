---
name: atlassian-lts
description: Facts about the Bitbucket, Jira (with JSM), and Confluence Data Center LTS images this factory hardens — catalog fields, build args, and how vulnerabilities in bundled libraries are remediated. Use when upgrading a product version or interpreting findings in /opt.
---

# Atlassian Data Center LTS images

| Image | Iron Bank repository | Build args |
|---|---|---|
| `bitbucket-lts` | `dsop/atlassian/bitbucket-data-center/bitbucket-lts` | `BITBUCKET_VERSION` |
| `jira-lts` | `dsop/atlassian/jira-data-center/jira-lts` | `JIRA_VERSION`, `JSM_VERSION` (kept equal) |
| `confluence-lts` | `dsop/atlassian/confluence-data-center/confluence-lts` | `CONFLUENCE_VERSION` |

All three build on the catalog base `ubi10-minimal` (`build.base.kind: catalog`;
`ubi9-minimal` is a rollback base),
publish under `atlassian/<image>`.

## Version changes

A product upgrade touches, together:

1. `catalog/images/<image>.yaml` — `product.version` and every `build.buildArgs` value.
2. The Dockerfile `ARG <PRODUCT>_VERSION=` lines (via the overlay or upstream).
3. `hardening_manifest.yaml` — `tags[0]` and the product tarball resource
   (`url`, `filename`, checksum). New tarballs need intake to re-lock.

Stay on an Atlassian **LTS** line unless a human explicitly decides otherwise.
Never downgrade.

## Runtime behaviour

The pipeline does not run these images; product start-up, ports, users and
`rpm -V` integrity are verified by the consuming environment (`docs/scope.md`).
Call out changes to ports, users, UIDs, or install paths in the change
description so that environment can adjust its tests.

## Vulnerabilities in bundled libraries

Findings under `/opt/<product>/...` are libraries Atlassian ships inside the
product (`inApplicationArchive: true`). They are remediated **only** by upgrading
the whole product to an LTS release that bundles the fixed library. Replacing
individual JARs breaks vendor support and is not acceptable. If no such release
exists, the finding is blocked pending the vendor; humans decide on exceptions.

## Reading scan results for these images

The SBOM covers every layer, including packages replaced in later layers. For
runtime triage, a Syft SBOM with `--scope squashed` scanned separately shows
only what is in the final filesystem; keep both and record the digest and
database date. A passing test stage or `assessmentPassed: true` says nothing
about vulnerability thresholds — only the gate result does.
