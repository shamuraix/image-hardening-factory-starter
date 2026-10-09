# Review checklist for pipeline, policy, and trust-boundary changes

## Quick summary

This repository baseline uses delegated scanner assessment artifacts and policy
gating for release decisions. Legacy vendor-specific guidance has been removed
from active operations and implementation docs.

---

## Current review focus

When reviewing new changes, prioritize:

1. **Digest continuity**
   - Every stage must reference and verify the exact candidate digest.
2. **Evidence integrity**
   - Gate, assessment, provenance, and signatures must align to one candidate.
3. **Policy behavior**
   - Blocking findings must deny release; fixable high/critical findings outside
     the application archive should produce warnings as configured.
4. **Configuration authority**
   - Runtime behavior should come from repository configuration/manifests, not
     hard-coded environment assumptions.
5. **Promotion safety**
   - Promotion must preserve content digest and re-verify signatures/attestations.

6. **Pipeline trust boundaries (Tekton / PaC)**
   - Pull-request runs never bind write, signing, release, or SCM-bot secrets.
   - Secrets are scoped to one step; steps holding them never run workspace code
     after PR code or an agent ran.
   - Consumers verify seals (`artifacts.sh verify`) before reading evidence.
   - Generated PipelineRuns change only via `factory/tekton.py`.
7. **Agent guardrails**
   - Personas keep `--bare`, `dontAsk`, caps, and strict schemas; writable paths
     stay minimal; the broker validates from a fresh clone.

## Reviewer checklist

- Validate updated schema and catalog compatibility.
- Validate delegated scanner handoff and assessment status generation.
- Validate release gate input/output contracts.
- Validate documentation updates for newcomer clarity and operator accuracy.
