# AI agents for factory operations

[Project overview](../README.md) · [Architecture](architecture.md) · [Operations](operations.md)

The factory uses **Claude Code** agents for the recurring operational work that
keeps three Iron Bank Atlassian images releasable: explaining failures, fixing
vulnerabilities, following upstream, briefing approvers, pruning exceptions,
and reviewing risky pipeline changes. Agents **propose**; humans and the
pipeline **decide**. No agent can merge, sign, publish, approve, or add a
vulnerability exception.

## Personas

Each persona is a Claude Code subagent in `.claude/agents/<name>.md`. The same
file serves interactive sessions and CI. An `x-factory` frontmatter block
(ignored by Claude Code) declares the CI contract: output schema, budget, modes,
and the exact paths it may write.

| Persona | When it runs | Reads | Produces | May write |
|---|---|---|---|---|
| `failure-triage` | `finally` of any failed build or release run | stage log tails, evidence summaries, task statuses | PR/commit comment: category, root cause, next steps | nothing |
| `cve-remediation` | gate deny on `main`, nightly rescan, or base-release rebuild | blocking findings, SBOM components, catalog, exceptions, internal repository candidates | draft PR + plan | `overlays/`, `catalog/images/` (revision, version, build args only), `tests/profiles/` |
| `upstream-sync` | weekday schedule | per-image upstream reconnaissance: new Repo One head, key-file diffs, manifest tags/args, overlay trial-apply results | draft PR with rebased patches and moved pins | `overlays/` (incl. deletes), `catalog/images/`, `vendir/config.yml`, `tests/profiles/` |
| `release-readiness` | after quarantine import on `main` | gate result and warnings, exceptions in scope, compliance/test results, previous release request | body of the release-request PR | nothing |
| `exception-steward` | weekly schedule | approved exceptions, evidence of each image's current release | draft PR removing stale exceptions + review list | `policies/exceptions/approved.json` — **removals only** |
| `pipeline-reviewer` | PRs touching `.tekton/`, `deploy/`, `scripts/`, `policies/`, `factory/`, `.claude/`, `agents/`; or `/review` | PR diff, checklist, repository at the PR revision | PR comment with verdict and findings | nothing |

Skills in `.claude/skills/` hold reusable domain procedures that personas preload:
`ironbank-overlays`, `atlassian-lts`, `evidence-model`, `tekton-factory`.

## How a CI agent run works

`.tekton/tasks/factory-claude-agent.yaml` runs one persona as five sequential
containers in one pod. Credentials never share a container, or a lifetime, with
the agent.

```mermaid
sequenceDiagram
    participant C as context (no secrets)
    participant A as agent (LLM gateway token)
    participant V as validate (no secrets)
    participant P as publish (SCM bot token)
    participant R as report (PaC provider token)
    C->>C: verify checkout == commit; private clone; gather inputs
    C->>A: agent/context, agent/repo
    A->>A: claude --bare -p ... (dontAsk, caps, JSON schema)
    A->>A: scrub own token from outputs
    A->>V: result.json, edits in agent/repo
    V->>V: schema check, write-policy check, change.patch, report.md
    V->>P: patch + report (data only)
    P->>P: fresh clone of pinned commit, re-validate, push branch, open draft PR
    V->>R: report.md (data only)
    R->>R: inline curl comment (no workspace code)
```

The CLI invocation is built by `factory.agents.build_command` and unit tested:

```text
claude --bare -p "<task prompt>"
  --model <persona model> --append-system-prompt-file <CLAUDE.md + persona + skills + run contract>
  --settings agents/ci/settings.json
  --tools <persona tools> --allowedTools <Read/Grep/Glob, Edit(/<writable>/**), Bash(<allowlist>)>
  --disallowedTools WebFetch WebSearch
  --permission-mode dontAsk --permission-prompts none
  --max-turns <n> --max-budget-usd <usd>
  --output-format json --json-schema <persona schema>
  --add-dir <context> --no-session-persistence
```

Why `--bare`: without it, `claude -p` loads hooks, MCP servers, and settings from
the working directory with no trust prompt. Bare mode loads only what the
command line passes, so a malicious change cannot smuggle a hook or MCP server
into an agent run.

## Guardrails, outermost first

1. **Trigger scope** — agent PipelineRuns come from the default branch; PR runs
   never bind the SCM bot (`scm-secret` defaults to a non-existent Secret) and the
   admission policy enforces that at pod creation.
2. **Network** — agent pods reach only the LLM gateway and the git host
   (`deploy/base/network-policies.yaml`). No internet, registry, or cluster API.
3. **Authoritative re-validation** — the publish step fetches the pinned commit
   afresh (objects hash-verified on transfer) and runs *that* code's
   `factory.cli agent-check`: path allowlist, protected prefixes, no symlinks,
   deletions only where allowed, frozen catalog policy fields, revision/vendir
   consistency, exceptions remove-only. The agent's output is only data.
4. **Human review** — every proposal is a draft change request; branch
   protection, CODEOWNERS, and the full factory pipeline decide.
5. **In-session controls** (defence in depth) — `dontAsk` mode with narrow allow
   rules, deny rules in `agents/ci/settings.json`, the
   `.claude/hooks/guard-paths.sh` PreToolUse hook, turn/budget/time caps, and
   strict JSON Schemas.
6. **Output hygiene** — the agent step redacts its own gateway token from every
   output and discards proposals containing it; reports neutralise `@`-mentions.

What remains: the gateway token is readable by the agent process, so scope it to
model access, rate-limit it, and rotate it. Agent text in comments is untrusted
and labelled as advisory.

## Model access

Point `ANTHROPIC_BASE_URL` (ConfigMap `factory-settings`) at your organization's
Anthropic-format LLM gateway and store its token in Secret `factory-ai-gateway`.
Personas use the `sonnet` and `opus` aliases; if the gateway exposes its own model
names, set `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL`, or
override per persona with `FACTORY_AGENT_MODEL_<PERSONA>` (for example
`FACTORY_AGENT_MODEL_UPSTREAM_SYNC`). The agent image pins the Claude Code CLI
version (`tools/versions.lock.yaml`, `toolchain/Containerfile.factory-agent`).

## Cost and runtime controls

| Persona | Model | Max turns | Budget (USD) |
|---|---|---|---|
| failure-triage | sonnet | 25 | 2 |
| release-readiness | sonnet | 20 | 2 |
| exception-steward | sonnet | 25 | 3 |
| pipeline-reviewer | opus | 30 | 5 |
| cve-remediation | opus | 40 | 8 |
| upstream-sync | opus | 60 | 12 |

Every run is also bounded by `FACTORY_AGENT_TIMEOUT` (default 1500s). The
`claude` JSON result records `total_cost_usd` and `num_turns`; the agent step
prints both so they appear in TaskRun logs.

## Using the agents interactively

In a Claude Code session at the repository root, the personas are available as
subagents and the same guard hook asks before edits to trust-boundary files and
blocks edits to generated PipelineRuns:

- "Have failure-triage look at `work/jira-lts` from my local build."
- "Use upstream-sync to rebase the confluence-lts overlays onto the new Repo One head."
- "Ask pipeline-reviewer to review my change to `.tekton/tasks/factory-quarantine.yaml`."

Locally there is no fresh-clone broker, so review diffs yourself before pushing;
CI will validate them again.

## Adding or changing a persona

1. Add `.claude/agents/<name>.md` with `name`, `description`, `tools`, `model`,
   `maxTurns`, `skills`, and `x-factory` (`schema`, `budgetUsd`, `modes`, and for
   writers `writablePaths`, optional `allowDeletes`, `bashAllow`).
2. Add `agents/schemas/<name>.schema.json` (strict: `additionalProperties: false`).
3. Add a context recipe in `scripts/agents/collect_context.sh`.
4. Add a trigger in `factory/tekton.py` (and a pipeline task if it needs new
   context), then `make tekton-render`.
5. `make ci` — `tests/unit/test_agents.py` loads every persona, validates its
   schema, and checks the generated command for the required safety flags.

`factory.agents.load_agent` refuses definitions that request `WebFetch`/`WebSearch`,
give report-only personas `Edit`/`Write`, or make protected paths writable.

## Evaluating agents before trusting them more

Keep agents at "propose" and measure before widening anything:

- **Triage accuracy** — sample triage comments weekly; track whether `category`
  and `rootCause` matched what humans found.
- **Proposal acceptance** — share of `agent-proposed` PRs merged unchanged,
  merged with edits, or closed; and how many passed the factory pipeline first time.
- **Rejections** — `change-rejected` outcomes mean an agent tried to step outside
  its contract; investigate each one.
- **Cost** — `total_cost_usd` per persona per week against the budgets above.
