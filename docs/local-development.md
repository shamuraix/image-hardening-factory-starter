# Local development

[Project overview](../README.md) · [Operations](operations.md)

## Quick start (new contributors)

### Dependencies

Install these first:

- Python 3.11+
- Git, Curl
- Podman, Skopeo
- Lima (`limactl`) with the `template://buildkit` VM
- BuildKit client (`buildctl`) pointed at the Lima BuildKit socket
- `jq`, `yq`, `umoci`

Use pinned tool versions from `tools/versions.lock.yaml` whenever possible.

### Fastest local build/test/assessment flow

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

limactl start --name factory-buildkit template://buildkit
export FACTORY_BUILDKIT_LIMA_INSTANCE=factory-buildkit
make local-build IMAGE=ubi9-minimal LOCAL_USE_UPSTREAM_UBI_REPOS=true
make local-test IMAGE=ubi9-minimal
make local-assessment IMAGE=ubi9-minimal
```

### Choose an RPM source mode

| Mode | Example |
|---|---|
| Direct public UBI CDN | `make local-build IMAGE=ubi9-minimal LOCAL_USE_UPSTREAM_UBI_REPOS=true` |
| Artifactory pull-through cache | `make local-build IMAGE=ubi9-minimal` (with `ARTIFACTORY_URL`, `ARTIFACTORY_READ_TOKEN`, `LOCAL_RPM_CACHE_REPOSITORY`) |
| Existing local repository directory | `make local-build IMAGE=ubi9-minimal LOCAL_RPM_REPO_DIR=/absolute/path/to/repo` |

All local outputs remain development-only and are not releasable artifacts.

## Advanced local development reference

### Build modes

The local development workflow uses Podman, a Lima BuildKit VM, and a
temporary loopback OCI registry. Three RPM source modes are available:

| Mode | Flag | Credentials required |
|---|---|---|
| **Direct public CDN** | `LOCAL_USE_UPSTREAM_UBI_REPOS=true` | None — UBI is publicly accessible |
| **Artifactory pull-through cache** | `ARTIFACTORY_URL`, `ARTIFACTORY_READ_TOKEN`, `LOCAL_RPM_CACHE_REPOSITORY` | Artifactory token |
| **Complete local mirror directory** | `LOCAL_RPM_REPO_DIR=/path/to/repo` | None |

The direct CDN mode builds against `cdn-ubi.redhat.com` without Artifactory.
It is convenient for initial setup and zero-credential development, but upstream
content may change between builds, making these builds non-reproducible.  The
resulting lock is marked `localDevelopment: true` and cannot enter import,
signing, or promotion.

The Artifactory pull-through cache mode fetches RPMs via the internal Artifactory
remote repository. The complete local mirror mode serves an existing local RPM
repository from a loopback HTTP server. Both modes also produce development-only
locks.

The workflow applies the catalog overlay, downloads and checksum-verifies
manifest resources, generates a clearly marked development resource lock, and
publishes the result to both an OCI archive and the local registry.  Catalog
base images are built automatically before an application image.

Prerequisites are Python 3.11+, Git, Curl, Podman, Skopeo, `yq`, `jq`,
`limactl`, and `buildctl`. Start the BuildKit VM with
`limactl start --name factory-buildkit template://buildkit` and set
`FACTORY_BUILDKIT_LIMA_INSTANCE=factory-buildkit` (or set
`FACTORY_BUILDKIT_ADDR` directly to a BuildKit socket address).
Use the versions recorded in `tools/versions.lock.yaml`. Umoci is also required
for malware and compliance scans.
For local mirror testing, `LOCAL_RPM_REPO_DIR` must point to a complete RPM
repository containing
`repodata/repomd.xml`. Signature checking remains enabled by default, so the
repository must also contain valid RPM and repository signatures trusted by
the source image.

Builds call `buildctl` against the running Lima BuildKit instance.
The local workflow requests host networking to reach its loopback services;
this is the local user's network, not a privileged container network.
Local builds keep BuildKit's default sandbox behavior from the Lima template.
Do not run the build as root to work around missing user-namespace support.

```bash
make local-build IMAGE=ubi9-minimal
```

Set `LOCAL_RPM_CACHE_REPOSITORY` to the Artifactory remote-repository key before
using the connected workflow. The repository must proxy
`https://cdn-ubi.redhat.com` while retaining the upstream
`content/public/ubi` path:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_RPM_CACHE_REPOSITORY="${LOCAL_RPM_CACHE_REPOSITORY:?}"
```

To build an Atlassian image and its UBI dependency:

```bash
make local-build IMAGE=jira-lts
```

To build from a complete local mirror instead of Artifactory:

```bash
make local-build IMAGE=ubi9-minimal \
  LOCAL_RPM_REPO_DIR=/absolute/path/to/ubi9-repo
```

By default, sources are cloned from each catalog entry's upstream URL. For
offline development, arrange pinned local mirrors as `<root>/<image>` and set:

```bash
export LOCAL_SOURCE_ROOT=/absolute/path/to/source-mirrors
```

The outputs are written to `work/<image>/image.oci.tar`,
`work/<image>/image-metadata.json`, and `work/<image>/resource-lock.json`. The
lock contains `"localDevelopment": true`; it is not signed and must never be
used for quarantine import or release. Run the image's baseline test profile
after building with:

```bash
make local-test \
  IMAGE=ubi9-minimal
```

Useful overrides include `LOCAL_REGISTRY` (loopback only, default
`127.0.0.1:5000`), `LOCAL_RPM_PORT` (default `18080`), and
`LOCAL_KEEP_REGISTRY=true`. The internal cache defaults to the URL
`${ARTIFACTORY_URL}/artifactory/${LOCAL_RPM_CACHE_REPOSITORY}/content/public/ubi`.
Set `LOCAL_RPM_CACHE_UBI_ROOT_URL` when the Artifactory remote repository maps
the upstream path at a different root. Setting `LOCAL_RPM_GPGCHECK=0` or
`LOCAL_RPM_REPO_GPGCHECK=0` is available only for disposable development data
and weakens parity with the production build. `LOCAL_RPM_SSLVERIFY=0` also
disables repository TLS verification and should be used only as a last-resort
diagnostic override.

The generated repository configuration is transferred using a BuildKit secret,
not a host bind mount requiring SELinux `:Z` relabeling. Newer Skopeo releases
may also refuse to copy upstream transport
signatures into local registries or OCI archives that cannot store them; the
local workflow explicitly removes those transport signatures while retaining
digest verification and retries transient registry failures.

If local HTTPS access requires an additional corporate CA, pass a single PEM
certificate with `LOCAL_CA_CERT`. For UBI 9 local builds, the certificate is
copied into the development context and activated before the first RPM access:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_CA_CERT=/absolute/path/to/corporate-ca.crt
```

This adds the CA to the resulting development image and therefore expands its
trust store. Review that addition before manually placing the image in a shared
quarantine repository. Do not pass a bundle of unrelated trust roots when the
specific issuing CA is available.

If only the host trust bundle is available, set `LOCAL_CA_BUNDLE` instead. The
local workflow passes the bundle to Curl for repository metadata downloads and,
when `LOCAL_CA_CERT` is unset, installs it in the UBI 9 development image before
the first RPM access:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_CA_BUNDLE=/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
```

When both variables are set, `LOCAL_CA_BUNDLE` configures host Curl and
`LOCAL_CA_CERT` is the certificate installed in the image.

For the fastest zero-credential development build, set `LOCAL_USE_UPSTREAM_UBI_REPOS=true`
to pull RPMs directly from the public UBI CDN:

```bash
make local-build IMAGE=ubi9-minimal LOCAL_USE_UPSTREAM_UBI_REPOS=true
```

This mode fetches BaseOS and AppStream from `cdn-ubi.redhat.com`, requires no
`ARTIFACTORY_URL`, `ARTIFACTORY_READ_TOKEN`, or `LOCAL_RPM_CACHE_REPOSITORY`,
and keeps RPM GPG checking and TLS verification enabled.  Both channel
`repomd.xml` documents are fetched and hashed into a deterministic composite
metadata digest; the CDN origin is recorded as `developmentSource` in the lock.
Because upstream content can change between builds, these direct CDN builds are
convenient but non-reproducible.  The lock remains marked
`"localDevelopment": true` and cannot be imported, signed, or promoted.

For the simplest Artifactory-backed connected development build, set the two
credentials and run the target directly:

```bash
make local-build IMAGE=ubi9-minimal
```

The workflow selects UBI 9 or UBI 10 from the catalog dependency and requests
BaseOS and AppStream only from the authenticated internal cache. It downloads
and hashes both `repomd.xml` documents and records a composite metadata digest
in the local development lock. The generated repo file enables both channels
and is mounted as a required BuildKit secret inside a tmpfs over
`/etc/yum.repos.d` during every `RUN`. This masks repository files inherited from the upstream UBI base
image while leaving the directory writable for `librhsm` initialization. RPM
operations therefore cannot fall back to Red Hat's public CDN or other
repository files inherited from the base image.  Because pull-through metadata
can change, these local development locks remain ineligible for quarantine
import or promotion.

To evaluate an application against the UBI 10 canary without changing its
production catalog dependency, set the local base override:

```bash
make local-build \
  IMAGE=jira-lts \
  LOCAL_BASE_IMAGE_OVERRIDE=ubi10-minimal

make local-test \
  IMAGE=jira-lts \
  LOCAL_BASE_IMAGE_OVERRIDE=ubi10-minimal
```

The override is accepted only for application images and must name a catalog
base image. The workflow builds the selected base first, uses its exact local
OCI archive, selects the matching UBI RPM repositories, and records both the
catalog base and effective base under `developmentBaseOverride` in the local
lock. It does not alter `catalog/images/jira-lts.yaml`; production continues to
depend on UBI 9.

The Atlassian overlays receive `BASE_MAJOR` from the effective base. Their
legacy forced RPM removals remain active for UBI 9, but are skipped for UBI 10
because its Java 21 package requires libraries such as `cups-libs`. RPM
verification ignores timestamp-only changes caused by reproducible layer
timestamps, combines the application allowlist with the base allowlist, and
adds the reviewed UBI 10 hardening deviations from
`tests/profiles/base/rpm-verify.ubi10.allow`.

## Image integrity and local scan results

The base test uses `update-ca-trust extract` and verifies that the generated TLS
bundle is nonempty and contains certificates. `check` is not a supported
`update-ca-trust` command on these UBI releases.

The UBI 9 overlay reinstalls `tzdata`, `gnupg2`, `libpeas`, and `rpm` to restore
package payloads pruned from the upstream minimal image. RPM verification still
checks file contents, permissions, and dependencies. It ignores normalized
modification times and RPM ghost entries (which have no package payload and
include host-mounted `/proc` and `/sys`). Explicit path allowlists cover the
reviewed login banner, shell umasks, crypto-policy changes, and omitted systemd
presets; arbitrary missing files and dependency failures remain errors.

Application overlays retain RPM runtime dependencies, including `cups-libs`
for Java and `freetype` for font rendering. They do not force-remove these
packages with `rpm --nodeps`. Bitbucket restores `/usr/bin` to its RPM mode
after the source installation of Git. Confluence restores the RPM-owned `/opt`
directory to `root:root` after setting application file ownership.

A successful image test does not mean an image has no vulnerabilities. The
standard SBOM covers all layers, including packages replaced in later layers.
For runtime triage, generate an additional Syft SBOM with `--scope squashed`
and scan that separately. Keep both reports and identify the exact image digest
and vulnerability database date. Neither a completed scan nor its
`assessmentPassed` evidence field means the release vulnerability thresholds
passed; the policy gate evaluates findings separately.
