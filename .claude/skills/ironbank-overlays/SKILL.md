---
name: ironbank-overlays
description: How Iron Bank (Repo One) container sources are structured and how this factory layers overlay patches on them. Use when editing overlays/, rebasing patches onto a new upstream revision, or debugging prepare/validate failures.
---

# Iron Bank sources and factory overlays

## Upstream layout (Repo One, `repo1.dso.mil/dsop/...`)

Each image repository has at its root:

- `Dockerfile` — upstream build; Iron Bank versions use
  `ARG BASE_REGISTRY` / `BASE_IMAGE` / `BASE_TAG` and `FROM ${BASE_REGISTRY}/${BASE_IMAGE}:${BASE_TAG}`.
- `hardening_manifest.yaml` — `tags` (first tag is the product version), `args`
  (including `BASE_IMAGE`, `BASE_TAG`), `labels`, and `resources[]` (each with
  `url`, `filename`, and `validation: {type: sha256|sha512, value}`; OCI base
  resources use `docker://...@sha256:` URLs).
- Product scripts (entrypoints, config templates).

The factory never builds from Registry1. Intake (`factory/intake.py`) resolves
every manifest resource, verifies its checksum, uploads it to the internal
repository, and signs a **resource lock**; builds consume only locked inputs.

## How overlays apply

`scripts/prepare_context.sh` clones the internal mirror at `source.revision`,
then applies every `*.patch` under `source.overlay` in **sorted filename order**
with `git apply --check` followed by `git apply`. Then
`scripts/validate_context.py` enforces:

| Rule | Detail |
|---|---|
| Version | `hardening_manifest.yaml` `tags[0]` == catalog `product.version` |
| Build args | each catalog `build.buildArgs` `KEY: value` appears as `ARG KEY=value` in the Containerfile |
| Base | Containerfile contains `FROM ${BASE_REF}` (pipeline-supplied, digest-pinned) |
| No public bases | no `registry1.dso.mil` or `registry.access.redhat.com` in the Containerfile |
| Base tag and image | catalog-based images: manifest `args.BASE_TAG` == base catalog `product.version`, and `args.BASE_IMAGE` ends with the catalog base name |

BuildKit then denies any remote image/HTTP/Git source during the solve, so a
patch cannot sneak in network fetches.

## Current overlay series (Atlassian images)

- `0001-*` — swap the Registry1 base for `ARG BASE_REF` / `FROM ${BASE_REF}`, fix
  upstream cleanup bugs, set `BASE_IMAGE`/`BASE_TAG` to the internal base.
- `0002-ubi10-compatibility.patch` — keep the build working on both UBI 9 and UBI 10 (`BASE_MAJOR`).
- `0003-preserve-runtime-dependencies.patch` — keep packages the product needs at
  runtime that upstream cleanup removes.
- Confluence `0004-preserve-system-directory-owner.patch`.

## Rebasing onto a new upstream revision

1. Start from a clean checkout of the new head.
2. Apply patches one at a time with `git apply --3way <patch>`; resolve conflicts
   preserving the factory's intent, not the old line numbers.
3. After each patch, `git add -A` and write the patch with
   `git diff --cached <previous-state> > overlays/<image>/patches/<same-name>`,
   or diff file-by-file; patches must be plain `git diff` output with `a/` and
   `b/` prefixes, applying cleanly with `git apply --check` in sequence.
4. Keep names and numbers stable so review diffs stay readable. Drop a patch only
   when upstream now contains the identical change.
5. Move `source.revision` and the matching `vendir/config.yml` ref together.
6. Validate with all patches applied:
   `python3 scripts/validate_context.py catalog/images/<image>.yaml <context-dir>`.

## Things that look like fixes but are not

- Editing `hardening_manifest.yaml` resource checksums by hand — intake must
  re-lock; an agent cannot download artefacts.
- Removing `rpm -e` or cleanup lines from upstream to make an image "cleaner"
  — keep upstream's hardening intent unless the change is reviewed.
- Pointing `FROM` at anything other than `${BASE_REF}`.

## What the current overlays do (so you keep their intent when rebasing)

- `ubi9-minimal`: pin `FROM ${BASE_REF}`; run `update-ca-trust extract` before
  the first RPM access (`check` is not a supported `update-ca-trust` command on
  these UBI releases); reinstall `tzdata`, `gnupg2`, `libpeas`, and `rpm` to
  restore package payloads the upstream minimal image prunes, so `rpm -V`
  passes wherever the consuming environment runs it.
- `ubi10-minimal`: pin `FROM ${BASE_REF}`; run `update-ca-trust` before the
  first RPM access (as on UBI 9).
- Jira `0004`/Confluence `0005`: keep `AWS_WEB_IDENTITY_TOKEN_FILE` and the
  `ATL_ALLOWLIST_SENSITIVE_ENV_VARS` allowlist (exact names) in
  `unset_secure_vars`; Jira `0005`/Confluence `0006`: decode XML entities then
  escape the JDBC URL and password. Keep both
  when rebasing; `context-check` fails without them.
- Bitbucket `0004`: install and remove `zlib-ng-compat-devel` on UBI 10.
- Atlassian images: `ARG BASE_MAJOR` selects behaviour per UBI major. The
  upstream forced removals (`rpm -e --nodeps avahi-libs cups-libs`) stay on UBI
  9 and are skipped on UBI 10, whose Java 21 package needs `cups-libs`. Runtime
  dependencies such as `cups-libs` (Java) and `freetype` (fonts) are retained.
  Bitbucket restores `/usr/bin` to its RPM mode after installing Git from
  source; Confluence restores the RPM-owned `/opt` to `root:root` after
  setting application file ownership.

## RPM integrity

The pipeline does not run `rpm -V`: it never runs the candidate image
([docs/scope.md](../../../docs/scope.md)). The consuming environment owns that
check. Keep the overlays that restore pruned package payloads so the check can
pass there, and call out any overlay change that alters RPM-owned files.
