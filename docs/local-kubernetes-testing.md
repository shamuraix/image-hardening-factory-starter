# Local Kubernetes testing

## Tested configuration

The Linux harness passes with **rootful Podman → kind → containerd/crun →
non-root Jenkins agents**. Jenkins and a TLS-authenticated fixture registry run
inside kind. Factory pods use UID/GID 10001, `hostUsers: false`,
`procMount: Unmasked`, and a tested crun RuntimeClass. The node's default runtime
remains runc.

The kubelet allocates 262144 IDs per pod, containing the runner's full
`factory:100000:65536` subordinate range. The Debian runner grants file capabilities
to newuidmap/newgidmap and disables Podman's unnecessary default sysctls. The
node needs a system D-Bus service for crun's systemd cgroup manager.

These are diagnostic workloads with synthetic development images and evidence.
Passing this harness does not establish production FIPS compliance, network
isolation, FCS compatibility, or application qualification.

## Rootful Podman on the existing Linux host

Requirements: Linux, Podman, kind, kubectl, Skopeo, Git, Python 3.11+, jq, curl,
OpenSSL, and sudo access. Bootstrap downloads pinned Linux tools and Jenkins
plugins. Allow access to GitHub releases, Debian mirrors, Docker Hub and Jenkins
update sites. A starting allocation is 6 CPUs, 12 GiB RAM and 30 GiB free disk.

Run as your normal user from the repository root:

```bash
sudo -v
export KIND_EXPERIMENTAL_PROVIDER=podman
export FACTORY_HARNESS_ROOTFUL=true
export FACTORY_HARNESS_CLUSTER=factory-proc-fixed
export FACTORY_HARNESS_STATE=.local-factory/proc-fixed-kind
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
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind status
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind collect
```

Collection pins artifact downloads to the reported build number. To refresh
source and job definitions without rebuilding images, follow the
[harness README](../tests/integration/kind/README.md#updating-the-source-snapshot).
Source ConfigMaps use server-side apply to avoid duplicating the archive in a
size-limited annotation. The archive must still fit the ConfigMap's 1 MiB limit;
a larger repository should use an internal SCM/artifact server.

Jenkins build 5 completed **SUCCESS** with 98 unit tests, 12 OPA
policy tests, five catalogs, real build/export and image execution, failing-RUN
rejection/cleanup, registry import/signing/promotion/retry, and artifact handoff
into a second fresh pod. Evidence: `.local-factory/proc-fixed-kind/results/5/`.

## Runtime troubleshooting

Start with pod events when containers are waiting; a log request can return
HTTP 400 before the container exists. Avoid sharing unredacted pod environment
variables, which include Jenkins agent credentials.

```bash
kubectl --kubeconfig .local-factory/proc-fixed-kind/kubeconfig -n factory-harness get events --sort-by=.lastTimestamp
sudo -v
tests/integration/kind/node-diagnostics.sh
```

`node-diagnostics.sh` reads node mappings, mount layout, runtime configuration
and recent security denials. The controlled runtime comparison is:

```bash
sudo -v
python3 tests/integration/kind/probe-crun.py --state .local-factory/proc-fixed-kind
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-kind refresh
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
- Runc failed sandbox sysfs mounting with user namespaces in kind. Editing the
  OCI base spec was ineffective because containerd 2.3 generates a separate
  sandbox spec. That experiment was rolled back; its obsolete scripts were removed.
- Crun handled the sandbox after its system D-Bus prerequisite was supplied.
  Historical diagnostics remain in ignored state directories; no host-wide
  sysctl changes were made.

## Alternative: Rancher Desktop with Moby plus kind

This path is implemented but **not acceptance-tested**, including native arm64.
Rancher Desktop supports Apple Silicon and Intel Macs; its documented Linux
requirements specify x86_64 and hardware virtualization. Check the official
[installation requirements](https://docs.rancherdesktop.io/getting-started/installation/).
Select [Moby](https://docs.rancherdesktop.io/ui/preferences/container-engine/general/)
and disable built-in Kubernetes when using kind, to avoid running two clusters.

Install the same host tools. On Apple Silicon use native arm64 binaries;
the harness downloads Linux tools for the node architecture. Then use separate
state and ports:

```bash
export DOCKER_CONTEXT=rancher-desktop
export KIND_EXPERIMENTAL_PROVIDER=docker
export FACTORY_HARNESS_ROOTFUL=false
export FACTORY_HARNESS_CLUSTER=factory-rancher
export FACTORY_HARNESS_STATE=.local-factory/rancher-kind
export FACTORY_HARNESS_JENKINS_PORT=18081
export FACTORY_HARNESS_REGISTRY_PORT=15444
tests/integration/kind/up.sh
```

The Podman-specific crun provisioner does not configure Docker nodes. Run the
runtime preflight and adapt the node runtime if the same sysfs issue appears;
changing providers alone is not a confirmed fix. Direct Rancher Desktop
Kubernetes still needs image-loading and endpoint adaptation. Confirm every
production image and application supports the target architecture.

## Teardown

Collect evidence first. Jenkins and registry use disposable emptyDir storage.

```bash
sudo -v
FACTORY_HARNESS_STATE=.local-factory/proc-fixed-kind tests/integration/kind/down.sh
```

Only the recorded harness cluster is deleted; saved artifacts remain on disk.
Cleanup of source code does not itself tear down a running harness.
