# Reviewer Templates

These files are the source of truth for cheap, repeatable subagent reviews in this repo.

They are intentionally additive:

- no runtime coupling to the product
- no new loader or spawning automation yet
- no dependence on Claude quota or Claude-specific review features

Use them by opening the relevant template file and pasting its prompt body into a `spawn_agent` request.

## Template Set

### `diff-sanity-spark.md`

Use for the cheapest first pass after a bounded task.

- model: `gpt-5.3-codex-spark`
- reasoning: `high`
- goal: catch obvious contradictions, wiring mistakes, missing tests, and suspicious diffs quickly
- scope: shallow diff review only, not a system-level or fallback-path sign-off
- not a final sign-off

### `spec-challenge-mini.md`

Use before implementation when a spec, plan, or design doc needs a critical pass.

- model: `gpt-5.4-mini`
- reasoning: `medium`
- goal: challenge assumptions, rollout gaps, safety holes, and unverifiable claims

### `critical-task-review-mini.md`

Use after a worker finishes a task and before accepting the result.

- model: `gpt-5.4-mini`
- reasoning: `medium`
- goal: review changed behavior, regressions, operational risk, fallback behavior, and missing tests
- this should replace most ad hoc uses of the expensive skeptic reviewer

### `final-risk-gate.md`

Use sparingly for broad, high-risk, or completion-gate reviews.

- model: `gpt-5.4`
- reasoning: `high`
- goal: final cross-file risk review before claiming a risky change is done
- reserve for wide-surface changes that cross task boundaries or make broad user-facing guarantees

## Default Review Ladder

1. Run `diff-sanity-spark.md` after a bounded worker task.
2. If the task changes behavior or config semantics, run `critical-task-review-mini.md`.
3. Run `final-risk-gate.md` only when at least one of these is true:
   - more than one completed task is being accepted together
   - the change spans manager plus host adapter behavior
   - the change alters packaging, console scripts, or MCP startup surface and also changes runtime behavior
   - the claim is broader than a single task, such as backward compatibility or system-wide setup correctness

For spec or planning work, swap step 1 for `spec-challenge-mini.md`.

## Repo-Specific Review Rules

Every template in this folder assumes the reviewer should enforce these rules:

- prefer the smallest real fix
- inspect files before making claims
- prefer `uv` for Python workflows
- do not accept `python -m ...` as the long-term system-wide interface when a stable console script is the intended user surface
- for config-mutating integrations, check effective scope, shadowing, idempotency, remove semantics, backup behavior, and atomic writes
- do not treat host `list` or `get` output as the only source of truth when local config files define the effective state
- do not claim verification without fresh command evidence or direct file-state confirmation

## When Claude Has No Quota

Do not block review work on Claude availability.

The default path is:

- quick pass with `gpt-5.3-codex-spark`
- serious task review with `gpt-5.4-mini`
- `gpt-5.4` only for explicit final risk gates
