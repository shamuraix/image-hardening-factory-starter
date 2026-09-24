# Harness readiness and production acceptance

## Quick status

The Kubernetes harness is suitable for repository workflow validation
(build, evidence, assessment, gate, import/sign/promote) using synthetic data.
It is not by itself production acceptance.

Current local evidence confirms:

- unit tests and policy tests pass
- end-to-end harness workflow passes with disposable fixtures
- release verification enforces required signed evidence

---

## Advanced acceptance checklist

### What the harness validates well

- script/stage wiring between build, assessment, and release stages
- digest identity continuity through import/sign/promotion
- failure propagation and artifact retention behavior
- delegated scanner assessment artifact handoff and gate integration

### What still requires environment-level acceptance

1. **Production runner parity**
   - confirm runtime/toolchain versions and security context match production
2. **Credential/RBAC separation**
   - verify intake/build/release permissions are isolated in production
3. **Registry policy controls**
   - verify immutability, referrer behavior, retention, and TLS/certificate posture
4. **Network and egress controls**
   - verify production egress restrictions and offline rules
5. **Application qualification**
   - run real product fixtures and approved compliance profiles

### Operational note

Harness artifacts under `.local-factory/` may include test credentials and must
remain local and uncommitted.
