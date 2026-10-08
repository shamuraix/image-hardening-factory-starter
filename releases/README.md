# Release requests

`releases/<environment>/<image>.yaml` (environments: `commercial`, `gov1`, `gov2`).

- The build pipeline writes these files deterministically from sealed evidence and
  opens a change request; you should not need to author one by hand.
- **Merging a change to one of these files signs and promotes the candidate it
  names.** The merger is recorded as the approver; gov environments require an
  approver matching `FACTORY_GOV_APPROVER_PATTERN` and the CODEOWNERS for that path.
- Exactly one request may change per merge.
- The file at HEAD is the latest release requested for that environment; git
  history is the release log. Roll back by re-requesting an earlier digest.

Schema: `factory/schemas/release-request.schema.json`. Validate with
`make release-requests`.
