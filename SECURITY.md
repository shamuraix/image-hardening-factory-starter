# Security policy

Report suspected vulnerabilities through the organization's internal security
response process. Do not include credentials, proprietary Atlassian archives,
scanner databases, SBOMs from restricted products, or customer data in a public
issue.

The following changes require protected CODEOWNER approval:

- Release policy, exceptions and VEX handling.
- Signing, attestation, importer or promotion code.
- Tekton/Pipelines-as-Code trust boundaries: trigger provenance, per-step credential scope,
  NetworkPolicies, the pull-request secret admission policy, and stage seal verification.
- Claude Code agent isolation: bare-mode invocation, permission rules, and the fresh-clone
  change broker.
- Cosign key material, Artifactory signing identities or key references.
- AI prompts, schemas, tools or writable-path policy.
- Scanner thresholds or database-freshness policy.

AI-generated changes must never be treated as a security approval. They enter
the same clean-checkout build, test, scan and human-review process as any other
untrusted contribution.
