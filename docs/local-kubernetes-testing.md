# Local Kubernetes testing

## Quick start

Use this harness when you need an end-to-end disposable validation path for the
repository workflow (build, evidence, gate, import/sign/promote mechanics).

Minimal flow:

1. Satisfy host prerequisites (Linux or macOS, `limactl`, kubectl, Skopeo, Python, jq, curl, OpenSSL, and `sudo` access for diagnostics/teardown helpers).
2. Start the harness:

   ```bash
   limactl start --name factory-k3s template://k3s
   export FACTORY_HARNESS_CLUSTER=factory-k3s
   export FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s
   export FACTORY_HARNESS_JENKINS_PORT=18083
   export FACTORY_HARNESS_REGISTRY_PORT=15446
   tests/integration/kind/up.sh
   python3 tests/integration/kind/jenkins.py --state "$FACTORY_HARNESS_STATE" run
   ```

3. Collect status/artifacts:

   ```bash
   python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s status
   python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s collect
   ```

4. Tear down:

   ```bash
   FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s tests/integration/kind/down.sh
   limactl stop factory-k3s
   ```

---

## Advanced harness details

## Tested configuration

The harness target is **Lima k3s template → Kubernetes → containerd/crun →
non-root Jenkins agents**. Jenkins and a TLS-authenticated fixture registry run
inside the local k3s cluster. Factory pods use UID/GID 10001, `hostUsers: false`,
`procMount: Unmasked`, and a tested crun RuntimeClass. The node's default runtime
remains runc.

The kubelet allocates 262144 IDs per pod, containing the runner's full
`factory:100000:65536` subordinate range. The Debian runner grants file capabilities
to newuidmap/newgidmap and disables Podman's unnecessary default sysctls. The
node needs a system D-Bus service for crun's systemd cgroup manager.

These are diagnostic workloads with synthetic development images and evidence.
Passing this harness does not establish production FIPS compliance, network
isolation, delegated-assessment compatibility, or application qualification.

## Lima k3s on the local host

Requirements: Linux or macOS, Lima (`limactl`), kubectl, Skopeo, Git, Python 3.11+, jq, curl,
OpenSSL, and sudo access for runtime diagnostics/teardown scripts. Bootstrap downloads pinned Linux tools and Jenkins
plugins. Allow access to GitHub releases, Debian mirrors, Docker Hub and Jenkins
update sites. A starting allocation is 6 CPUs, 12 GiB RAM and 30 GiB free disk.

Run as your normal user from the repository root:

```bash
limactl start --name factory-k3s template://k3s
export FACTORY_HARNESS_CLUSTER=factory-k3s
export FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s
export FACTORY_HARNESS_JENKINS_PORT=18083
export FACTORY_HARNESS_REGISTRY_PORT=15446
tests/integration/kind/up.sh
python3 tests/integration/kind/jenkins.py --state "$FACTORY_HARNESS_STATE" run
```

Bootstrap uses a dedicated kubeconfig and saves its provider, cluster and port
settings. Existing state must match those settings. Existing clusters with too
few IDs per pod are rejected; bootstrap does not silently reconfigure or delete
nodes. Ordinary rootless Podman's default ID allocation is too small for this
nested topology and is rejected before cluster creation.

For rootful Podman, bootstrap installs and probes crun when no usable saved
RuntimeClass exists. The successful class is saved in `STATE/runtime-class`;
Jenkins pipeline refreshes apply it to all harness jobs. Crun and D-Bus are
installed only inside the disposable node. A failed runtime probe restores the
original containerd configuration and removes its test RuntimeClass; installed
packages and the node-local D-Bus service remain available.

- Jenkins: http://127.0.0.1:18083, user `review`, generated password in
  `STATE/jenkins-password`.
- Registry: https://localhost:15446, public **test-only** credentials `oidc/harness`.
- CA certificate: `STATE/client-ca/ca.crt`; dedicated kubeconfig: `STATE/kubeconfig`.
- State and diagnostics are ignored by Git. Keep generated credentials out of commits.

## Results and source refresh

```bash
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s status
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s collect
```

Collection pins artifact downloads to the reported build number. To refresh
source and job definitions without rebuilding images, follow the
[harness README](../tests/integration/kind/README.md#refreshing-source-bundle-in-running-harness).
Source ConfigMaps use server-side apply to avoid duplicating the archive in a
size-limited annotation. The archive must still fit the ConfigMap's 1 MiB limit;
a larger repository should use an internal SCM/artifact server.

Jenkins build 5 completed **SUCCESS** with 98 unit tests, 12 OPA
policy tests, five catalogs, real build/export and image execution, failing-RUN
rejection/cleanup, registry import/signing/promotion/retry, and artifact handoff
into a second fresh pod. Evidence: `.local-factory/proc-fixed-k3s/results/5/`.

## Runtime troubleshooting

Start with pod events when containers are waiting; a log request can return
HTTP 400 before the container exists. Avoid sharing unredacted pod environment
variables, which include Jenkins agent credentials.

```bash
kubectl --kubeconfig .local-factory/proc-fixed-k3s/kubeconfig -n factory-harness get events --sort-by=.lastTimestamp
sudo -v
tests/integration/kind/node-diagnostics.sh
```

`node-diagnostics.sh` reads node mappings, mount layout, runtime configuration
and recent security denials. The controlled runtime comparison is:

```bash
sudo -v
python3 tests/integration/kind/probe-crun.py --state .local-factory/proc-fixed-k3s
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s refresh
```

It waits for kubelet to advertise the RuntimeClass's user-namespace support,
then tests sandbox startup, private `/proc` mounting and Podman's UID mapping.
Passing the probe selects the runtime; the full Jenkins test remains necessary.

The investigation established:

- Debian mapping helpers needed explicit SETUID/SETGID file capabilities.
  [Related Podman discussion](https://github.com/containers/podman/discussions/19931).
- Nested Podman required unmasked proc and no default ping-group sysctl.
  [Kubernetes proc configuration](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/)
  and [Podman issue](https://github.com/containers/podman/issues/13194).
- Runc failed sandbox sysfs mounting with user namespaces in local clusters. Editing the
  OCI base spec was ineffective because containerd 2.3 generates a separate
  sandbox spec. That experiment was rolled back; its obsolete scripts were removed.
- Crun handled the sandbox after its system D-Bus prerequisite was supplied.
  Historical diagnostics remain in ignored state directories; no host-wide
  sysctl changes were made.

## Alternative: additional Lima instances

For parallel test environments, start another named Lima k3s instance and use
separate state and ports:

```bash
limactl start --name factory-k3s-alt template://k3s
export FACTORY_HARNESS_CLUSTER=factory-k3s-alt
export FACTORY_HARNESS_STATE=.local-factory/k3s-alt
export FACTORY_HARNESS_JENKINS_PORT=18081
export FACTORY_HARNESS_REGISTRY_PORT=15444
tests/integration/kind/up.sh
```

Validate runtime behavior with the same preflight and diagnostics before relying
on alternate instances for review evidence.

## Teardown

Collect evidence first. Jenkins and registry use disposable emptyDir storage.

```bash
sudo -v
FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s tests/integration/kind/down.sh
limactl stop factory-k3s
```

Only the recorded harness cluster is deleted; saved artifacts remain on disk.
Cleanup of source code does not itself tear down a running harness.
