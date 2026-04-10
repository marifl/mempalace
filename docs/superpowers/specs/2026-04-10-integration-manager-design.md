# MemPalace Integration Manager Design

Date: 2026-04-10
Status: Proposed

## Goal

Add a first-party, one-command integration manager to MemPalace that can:

- autodiscover supported agent CLIs on the current machine
- configure MemPalace for those hosts from a single command
- support strict `uv`-based workflows without assuming `pip` or ad hoc virtualenvs
- remain additive, reversible through explicit removal, and low-risk for upstream syncs

The manager must not require rewriting the existing MCP, mining, search, or storage logic.

## Non-Goals

- Replacing existing `mempalace` commands such as `init`, `mine`, `search`, `status`, or `mcp`
- Requiring all existing plugin manifests and examples to be rewritten immediately
- Managing arbitrary third-party config outside of MemPalace-owned blocks
- Implementing cross-host abstractions for features MemPalace does not use

## Product Decision

MemPalace will add a new integration surface instead of mutating the current setup flow in place.

Recommended user model:

```bash
mempalace integrate
mempalace integrate claude codex gemini
mempalace integrate --dry-run
mempalace integrate --write
mempalace integrate remove
mempalace integrate remove claude codex --write
```

Behavior:

- `mempalace integrate`
  - autodiscovers supported hosts
  - builds a plan
  - presents the plan
  - asks for confirmation before writing
- `mempalace integrate claude codex`
  - targets only the named hosts
  - still plans first
  - asks for confirmation before writing unless `--write` is set
- `mempalace integrate --dry-run`
  - never writes
  - prints the full plan and proposed changes
- `mempalace integrate --write`
  - applies non-interactively
  - still prints the plan first
- `mempalace integrate remove`
  - autodiscovers MemPalace-managed integrations
  - plans removal only for MemPalace-owned entries
  - asks for confirmation before writing unless `--write` is set

## Safety Model

The manager only owns clearly marked MemPalace-managed blocks inside host configuration files.

Rules:

- Prefer host-native mutation commands over direct file editing when the host CLI supports them.
- Never rewrite whole host config files when a block update is sufficient.
- Never modify content outside MemPalace-owned markers.
- Always create a backup before the first write to each file.
- Be idempotent: repeated application must not duplicate entries.
- Prefer update-in-place of the MemPalace block over append-only writes.
- If a target file format cannot safely support managed blocks, report `cannot_apply` instead of forcing a write.
- Use atomic writes for direct file mutations: write temp file, fsync, validate, then rename.
- Re-parse every mutated file before finalizing success.
- Use best-effort file locking or single-process guards where host config races are plausible.

Managed block shape:

```text
# >>> MemPalace managed block >>>
...
# <<< MemPalace managed block <<<
```

JSON files cannot use comments directly, so block management there must be structural, not textual. For JSON-based configs, the manager should:

- write only MemPalace-owned keys
- preserve unrelated keys byte-for-byte where practical
- remove and replace only the MemPalace subtree on update

## `uv` Strategy

The integration manager must assume `uv` is the primary package manager.

Operational model:

- MemPalace should be installable once with `uv tool install ...`
- host integrations must prefer stable console entrypoints over `python -m ...`
- a dedicated MCP entrypoint must exist in Phase 1 as a package script

Required Phase 1 command surface:

- `mempalace`
- `mempalace-mcp`

This makes host configuration independent of:

- the user's active shell environment
- the location of a repo-local `.venv`
- the presence of `pip`
- the exact `python3` interpreter on `PATH`

`mempalace hook run ...` remains the stable hook entrypoint in Phase 1.

## Supported Hosts

Initial target hosts:

- Claude Code
- Codex CLI
- Gemini CLI

Each host adapter defines:

- how the host is discovered
- which scopes and precedence layers exist for that host
- how effective configuration is resolved
- which files are relevant
- whether MemPalace can be represented as a managed block
- how MCP registration is expressed
- how hooks are expressed
- how to render a dry-run preview

## CLI UX

### Command

```bash
mempalace integrate [hosts...] [--dry-run] [--write] [--palace PATH] [--scope auto|user|project]
mempalace integrate remove [hosts...] [--dry-run] [--write] [--scope auto|user|project]
```

### Arguments

- `hosts...`
  - optional explicit target list such as `claude`, `codex`, `gemini`
  - if omitted, autodiscovery is used

### Flags

- `--dry-run`
  - plan only
  - write nothing
- `--write`
  - apply without interactive confirmation
- `--palace PATH`
  - optional override for the palace location embedded in host config if needed
- `--scope`
  - defaults to `auto`
  - `auto` means “target the highest-precedence writable scope that will actually be effective”
  - adapters may reject unsupported scopes with `cannot_apply`

### Output model

The command should produce a plan with one entry per host:

- `create`
- `update`
- `skip`
- `cannot_apply`
- `not_found`

Each entry should include:

- host name
- requested scope
- effective scope
- shadowed-by or overridden-by information when present
- files that would be touched
- whether backup will be created
- whether host-native CLI mutation or direct file patching will be used
- MCP action summary
- hook action summary

## Internal Architecture

Add a dedicated integration package:

- `mempalace/integrations/__init__.py`
- `mempalace/integrations/base.py`
- `mempalace/integrations/manager.py`
- `mempalace/integrations/claude.py`
- `mempalace/integrations/codex.py`
- `mempalace/integrations/gemini.py`

Responsibilities:

- `base.py`
  - shared dataclasses and adapter protocol
- `manager.py`
  - host selection
  - autodiscovery orchestration
  - plan generation
  - confirmation flow
  - apply loop
- host adapters
  - discovery
  - file parsing/rendering
  - safe update semantics

## Data Model

Suggested core structures:

```python
IntegrationTarget
IntegrationPlan
IntegrationAction
IntegrationResult
HostDiscovery
```

Suggested action fields:

- `host`
- `path`
- `kind`
- `status`
- `summary`
- `before_excerpt`
- `after_excerpt`
- `backup_path`

## Discovery Rules

Autodiscovery must be conservative.

Examples:

- Claude Code
  - detect local, project, and user config layers
  - detect presence of `claude` on `PATH` if useful
- Codex CLI
  - detect global config plus repo-local plugin or project config layers
  - detect presence of `codex` on `PATH`
- Gemini CLI
  - detect user and project settings layers
  - detect presence of `gemini` on `PATH`

Discovery should not create host files eagerly unless the host is explicitly targeted or the user confirms creation from the plan.

Discovery must resolve effective host state, not just candidate files.

At minimum the plan must report:

- where MemPalace is already configured
- which scope is currently effective
- whether a higher-precedence config will shadow the proposed change
- whether the manager can safely mutate the effective target

## Write Semantics Per Host

### Claude Code

Target:

- effective Claude config scope selected by `auto` resolution or explicit `--scope`
- MCP registration through `claude mcp add/remove` when the CLI is available
- direct file patching only as a validated fallback
- hook config only if the host exposes a supported writable config location

Policy:

- prefer host-native CLI mutation over file editing
- detect and report local or project config that would shadow a user-level change
- do not silently write user config if a higher-precedence project or local config is effective

### Codex CLI

Target:

- effective Codex config scope selected by `auto` resolution or explicit `--scope`
- MCP registration through `codex mcp add/remove` when the CLI is available
- direct file patching only as a validated fallback

Policy:

- prefer host-native CLI mutation over file editing
- detect and report repo-local plugin or project config that would shadow a user-level change
- do not require copying `.codex-plugin` into every repo for global usage

### Gemini CLI

Target:

- effective Gemini settings scope selected by `auto` resolution or explicit `--scope`
- MCP registration through `gemini mcp add/remove` when the CLI is available
- direct JSON patching as fallback for MCP and as the primary path for hooks

Policy:

- patch only MemPalace-owned MCP and hook keys
- preserve unrelated Gemini settings
- detect and report project settings that would shadow a user-level change

Gemini hook support is not “just patch settings”.

Phase 1 Gemini requirements:

- extend `hooks_cli` with a `gemini` harness
- define Gemini stdin parsing and stdout response semantics explicitly
- map Gemini `PreCompress` host events to MemPalace internal `precompact` behavior
- use a stable command form such as `mempalace hook run --hook precompact --harness gemini`
- if the Gemini hook adapter is not implemented, the manager must mark Gemini hook integration as `cannot_apply` while still allowing MCP setup

## Interaction Model

When `--write` is not set:

1. discover targets
2. render plan
3. if `--dry-run`, exit
4. ask `Apply these changes? [y/N]`
5. write on explicit confirmation only

When `--write` is set:

1. discover targets
2. render plan
3. apply directly

When `remove` is used:

1. discover existing MemPalace-managed integration state
2. render a removal plan
3. remove only MemPalace-owned entries or host-native MCP registrations for MemPalace
4. preserve unrelated host configuration

## Failure Handling

Failure in one host must not erase successful writes for other hosts.

Rules:

- apply per host independently
- report per-host success or failure
- if a write fails after backup creation, point to the backup path
- do not continue modifying the same file after a parse error
- if host-native mutation succeeds but effective-state verification fails, report partial failure
- verify post-apply effective state when the host exposes an inspect or list command

## Backups

Backups should live under a MemPalace-owned directory, for example:

```text
~/.mempalace/backups/integrations/
```

Backup naming:

- host
- original filename
- timestamp

Backups are recovery artifacts, not the only reversal mechanism.

The manager must also support explicit removal of MemPalace-managed integration state via `mempalace integrate remove`.

## Relationship To Existing Repo Assets

This manager is additive.

Existing assets remain valid:

- `.claude-plugin/`
- `.codex-plugin/`
- example setup documents
- `mempalace mcp`

The new manager becomes the preferred user-facing setup path, but not an immediate hard dependency for legacy flows.

Where a host-native CLI exists, the manager should prefer that CLI over legacy repo-local plugin coupling.

## Testing Strategy

Add targeted unit tests for:

- autodiscovery by host
- effective-scope detection by host
- shadowed configuration detection
- explicit host selection
- dry-run produces no writes
- backups created before writes
- idempotent repeated application
- explicit removal removes only MemPalace-owned state
- unmanaged content remains unchanged
- MemPalace block update replaces prior MemPalace block only
- parse failures report `cannot_apply`
- direct file patches use atomic temp-file write and validate before replace
- host-native CLI mutation is preferred when available
- Gemini harness parsing and event mapping

Add CLI tests for:

- `mempalace integrate`
- `mempalace integrate claude codex`
- `mempalace integrate --dry-run`
- `mempalace integrate --write`
- `mempalace integrate remove`

Use temp directories and fixture config files rather than real dotfiles.

## Migration Plan

Phase 1:

- add a dedicated `mempalace-mcp` console script
- add integration manager and CLI surface
- add host adapters
- add effective-scope discovery and shadowing detection
- use host-native MCP mutation paths where available
- add Gemini harness support or explicitly surface Gemini hooks as `cannot_apply`
- add dry-run and write semantics
- add explicit removal flow
- add atomic direct-write path with validation
- keep legacy docs and flows intact

Phase 2:

- update docs to recommend `uv tool install`
- update manager defaults to use `mempalace-mcp` everywhere MCP registration is needed

Phase 3:

- optionally refresh plugin manifests and examples to align with the manager
- keep legacy instructions as fallback until confidence is high

## Open Questions

- Exact user-level config locations for each host should be validated before implementation.
- Exact scope names and precedence rules differ by host and must be adapter-owned, not globally guessed.
- Some hosts require structural JSON patching instead of textual block insertion.
- We should decide whether `mempalace mcp` becomes a compatibility shim around `mempalace-mcp` or remains documentation-only.

## Recommendation

Implement the manager as a new additive feature with:

- autodiscovery by default
- explicit host targeting when provided
- `--dry-run` for preview
- interactive confirmation by default
- `--write` for non-interactive application
- `remove` for explicit reversal
- effective-state detection before mutation
- host-native mutation paths where possible
- MemPalace-owned managed blocks only for direct file edits

This gives the desired one-command setup without turning MemPalace into a brittle dotfile overwriter.
