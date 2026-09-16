# Local Jenkins / Kubernetes / registry harness

This disposable harness exercises repository scripts in real Kubernetes pods.
It is separate from production deployment configuration and uses synthetic
images and predicates. It does not perform a real FCS or STIG assessment.

## Start and use

Run from the repository root on the Linux host (outside a filesystem/network
sandbox). Use a rootful Podman or Docker/Moby node with pod user-namespace support.
The typical rootless Podman allocation of 65536 IDs cannot support this harness's
nested Kubernetes namespaces; bootstrap rejects it before creating a cluster.
See the [runtime setup guide](../../../docs/local-kubernetes-testing.md#rootful-podman-on-the-existing-linux-host) for the separate rootful cluster command.
Other requirements are cgroup v2,
kind, kubectl, Skopeo, curl, OpenSSL, Python 3 and jq. The bootstrap downloads Linux yq and OPA. Downloads need
access to GitHub releases, Debian package mirrors, Docker Hub, and Jenkins
plugin update sites. Approximately 8 GiB free memory and 15 GiB free disk are
recommended for this small single-node setup.

```bash
sudo -v
export FACTORY_HARNESS_ROOTFUL=true
export FACTORY_HARNESS_CLUSTER=factory-proc-fixed
export FACTORY_HARNESS_STATE=.local-factory/proc-fixed-kind
export FACTORY_HARNESS_JENKINS_PORT=18083
export FACTORY_HARNESS_REGISTRY_PORT=15446
tests/integration/kind/up.sh
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind run
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind status
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind log
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind collect
```

- Jenkins: <http://127.0.0.1:18083/job/factory-harness/>
- Login: `review`; password file: `.local-factory/proc-fixed-kind/jenkins-password`.
- Registry: `https://localhost:15446`; CA: `.local-factory/proc-fixed-kind/client-ca/ca.crt`.
- Dedicated kubeconfig: `.local-factory/proc-fixed-kind/kubeconfig`.
- Namespace: `factory-harness`; cluster: `factory-proc-fixed`.
- Logs, exported images, keys, and generated configuration remain in ignored
  `.local-factory/proc-fixed-kind/`; do not add this directory to Git.

The registry uses the public test credentials `oidc` / `harness` and is for
disposable fixtures only. Published host ports bind to loopback. The generated certificate
lasts seven days. The cluster contains its own test Jenkins login and test
signing keys; no production credentials are required or used.

Use the dedicated kubeconfig explicitly for inspection:

```bash
kubectl --kubeconfig .local-factory/proc-fixed-kind/kubeconfig -n factory-harness get pods
```

## Tests

The `factory-harness` job runs:

1. Five catalog validations, the Python unit suite, and 12 OPA tests in an
   actual Jenkins Kubernetes agent.
2. A synthetic Ubuntu-based image through the actual `build_image.sh` and
   `run_buildkit.sh`, using the pinned native BuildKit worker and a registry
   seed supplied as an OCI archive. No RPM installation is attempted.
3. Rootless Podman execution of the produced image; a deliberately failing
   BuildKit RUN; and checking removal of per-build state.
4. Negative importer checks for a denied gate and a development lock.
5. The actual production import, signing, and promotion scripts against the
   local registry, with synthetic evidence explicitly labeled as such.
6. Negative authentication and unsigned-image checks, plus promotion retry.
7. Stash in one pod and checksum verification after unstash in a fresh pod.

Registry predicates, public keys, build metadata, and test summaries are
archived by Jenkins. Private signing keys stay in a temporary directory and are
removed after the registry test. The test password `harness-only` and registry
identity `harness` are deliberately non-production fixtures.

## Updating the source snapshot

Jenkins agents receive a ConfigMap containing a tar archive of non-ignored
repository files. Refresh it after changing the source or test scripts:

```bash
git ls-files --cached --others --exclude-standard -z |
  tar --null -T - -czf .local-factory/proc-fixed-kind/source.tar.gz
kubectl --kubeconfig .local-factory/proc-fixed-kind/kubeconfig -n factory-harness \
  create configmap factory-source \
  --from-file=source.tar.gz=.local-factory/proc-fixed-kind/source.tar.gz \
  --dry-run=client -o yaml |
  kubectl --kubeconfig .local-factory/proc-fixed-kind/kubeconfig apply \
    --server-side --field-manager=factory-harness --force-conflicts -f -
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind refresh
```

Use `up.sh` after changing images. The source archive must fit a Kubernetes
ConfigMap (1 MiB); large future repositories should use an internal SCM server
or artifact server instead. Registry and Jenkins storage use emptyDir, so a
replacement deployment pod loses its stored data. This is intentional for the
harness; collect the console log before tearing down or replacing Jenkins.

## Runtime differences and remaining prerequisites

- The runner is Debian-based with distro Skopeo/Podman, not the production UBI
  runner. BuildKit 0.33.0, RootlessKit 3.1.0, Cosign 2.6.0, and ORAS 1.3.0 are
  downloaded and checksum-verified, including native Linux yq and OPA.
- Runner pods use `hostUsers: false` and `procMount: Unmasked`. The kubelet
  allocates 262144 IDs per pod to contain the runner's `100000:65536` subordinate
  range. This requires a new cluster; bootstrap does not rewrite existing nodes.
  Host subordinate-ID files are not modified.
- Both agent containers use `/tmp/jenkins-agent`; this avoids an arbitrary UID
  failing to traverse the official inbound-agent image's home directory.
- Unit tests run with FACTORY_BUILD_ID and the process-sandbox override removed
  because existing tests assume a local build identity.
- Production SCM checkout, credential-provider isolation, Gov approval/RBAC,
  external Artifact Manager, and the complete intake job still require their
  deployment-specific configuration.
- Real FCS testing needs the licensed CLI plus test-tenant credentials and
  success/denial/error fixtures. Full UBI/application tests need signed source
  locks, RPM snapshots, product archives, SCAP data/tailoring and any required
  application fixtures.
- Distribution Registry tests establish OCI transport behavior, not Artifactory
  authorization, token exchange, immutability, or production referrer support.
- Default kind networking does not prove enforcement of the production
  NetworkPolicies. FIPS-node behavior is not established here.

## Verified baseline

Jenkins build 5 on `factory-proc-fixed` completed **SUCCESS**: unit/policy/catalog
checks, real build and execution, failing-build cleanup, authenticated registry
import/signing/promotion/retry, and artifact handoff to a fresh pod. Archived
results are in `.local-factory/proc-fixed-kind/results/5/`.

The rootful bootstrap provisions crun and system D-Bus inside the node, probes
user namespaces and private proc mounts, and saves the tested RuntimeClass.
Pipeline refreshes and stage-wrapper checks use that same saved class.
See [remaining production acceptance work](../../../docs/reviews/harness-remediation-plan.md#remaining-production-acceptance-work).

## Stop

```bash
sudo -v
FACTORY_HARNESS_STATE=.local-factory/proc-fixed-kind tests/integration/kind/down.sh
```

This deletes the recorded harness cluster, including disposable Jenkins and
registry storage. Saved artifacts remain on disk.

## Additional checks

```bash
python3 tests/integration/kind/jenkins-stage-checks.py --state .local-factory/proc-fixed-kind
```

That command creates three local test jobs using the production stage wrapper,
checks failed gates and blocking errors, and actually stops the cancellation job.
It preserves logs under the state directory's `stage-checks` folder.

`scanner-smoke.sh` runs on Linux with pinned harness tools on PATH. Populate
`STATE/security-data/grype` with `grype db update` and
`STATE/security-data/advisories/cisa-kev.json` from the official CISA feed first.
Then run with the same state/registry port variables as bootstrap. It exercises
real Syft/Grype, gate, import, signing and promotion; compliance and product-test
predicates are explicit fixtures. It requires no FCS credentials. On macOS run
this exercise in a Linux runner with the offline bundle mounted; Linux binaries
cannot execute directly on Darwin.

For a rootful comparison, run `sudo -v` and `tests/integration/kind/rootful-preflight.sh`
from your terminal. The separate kind mode uses `FACTORY_HARNESS_ROOTFUL=true`; see
[the complete commands](../../../docs/local-kubernetes-testing.md#rootful-podman-on-the-existing-linux-host).
