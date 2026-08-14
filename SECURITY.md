# Security policy

Report suspected vulnerabilities through the organization's internal security
response process. Do not include credentials, proprietary Atlassian archives,
scanner databases, SBOMs from restricted products, or customer data in a public
issue.

The following changes require protected CODEOWNER approval:

- Release policy, exceptions and VEX handling.
- Signing, attestation, importer or promotion code.
- GitLab runner trust boundaries and protected tags.
- Cosign key material, Artifactory signing identities or key references.
- AI prompts, schemas, tools or writable-path policy.
- Scanner thresholds or database-freshness policy.

AI-generated changes must never be treated as a security approval. They enter
the same clean-checkout build, test, scan and human-review process as any other
untrusted contribution.
