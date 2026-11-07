# Review checklist for pipeline, policy, and trust-boundary changes

[Project overview](../README.md) · [Architecture](architecture.md) · [Agents](agents.md)

Release decisions rest on delegated scanner assessments and the OPA gate. Review
changes against the table below; `pipeline-reviewer` uses the same list.

## What to check

| Area | Question |
|---|---|
| Digest continuity | Does every stage reference and verify the exact candidate digest? |
| Evidence integrity | Do the gate, assessment, provenance, and signatures all bind to one candidate? |
| Policy behaviour | Do blocking findings deny release? Do fixable high/critical findings outside the application archive warn, as configured? |
| Configuration authority | Does runtime behaviour come from repository configuration and manifests, not hard-coded environment assumptions? |
| Promotion safety | Does promotion preserve the content digest and re-verify signatures and attestations? |
| Secrets | Do PR runs never bind write, signing, release, or SCM-bot secrets? Is each secret scoped to one step, with no workspace code run after PR or agent code? |
| Seals | Do consumers verify seals (`artifacts.sh verify`) before reading evidence? |
| Generated files | Do generated PipelineRuns change only through `factory/tekton.py`? |
| Agent guardrails | Do personas keep `--bare`, `dontAsk`, caps, and strict schemas? Are writable paths minimal? Does the broker validate from a fresh clone? |

## Also confirm

- Schema and catalog changes are compatible (`make validate`).
- Scanner handoff and assessment status generation still work.
- Gate input and output contracts are unchanged, or changed on purpose.
- Docs match the change and are clear to a newcomer and accurate for operators.
