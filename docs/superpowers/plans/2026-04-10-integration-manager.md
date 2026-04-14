# MemPalace Integration Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-party `mempalace integrate` manager that configures MemPalace for Claude Code, Codex CLI, and Gemini CLI with autodiscovery, explicit host targeting, dry-run/apply/remove flows, `uv`-safe entrypoints, host-native mutation where possible, and safe fallback file patching.

**Architecture:** Add a new `mempalace.integrations` package with adapter-per-host boundaries plus a manager that plans, verifies, and applies integration changes. Phase 1 ships a stable `mempalace-mcp` console script, extends `hooks_cli` for Gemini, prefers host-native MCP registration commands when available, and only edits config files through atomic validated writes.

**Tech Stack:** Python 3.9+, argparse, pathlib, subprocess, json, pytest, hatchling console scripts

---

## File Map

### Create

- `mempalace/integrations/__init__.py`
- `mempalace/integrations/base.py`
- `mempalace/integrations/io.py`
- `mempalace/integrations/manager.py`
- `mempalace/integrations/claude.py`
- `mempalace/integrations/codex.py`
- `mempalace/integrations/gemini.py`
- `mempalace/mcp_main.py`
- `tests/test_integrations_base.py`
- `tests/test_integrations_manager.py`
- `tests/test_integrations_claude.py`
- `tests/test_integrations_codex.py`
- `tests/test_integrations_gemini.py`

### Modify

- `pyproject.toml`
- `mempalace/cli.py`
- `mempalace/mcp_server.py`
- `mempalace/hooks_cli.py`
- `mempalace/instructions/help.md`
- `examples/mcp_setup.md`
- `tests/test_cli.py`
- `tests/test_hooks_cli.py`
- `tests/test_version_consistency.py`

### Do Not Touch In Phase 1 Unless Required By Failing Tests

- `.claude-plugin/`
- `.codex-plugin/`
- `README.md`
- `examples/` except `examples/mcp_setup.md`

Those remain legacy-compatible and should not be coupled to the manager implementation.

## Phase 1 Decisions To Lock Before Coding

- `mempalace-mcp` is required in Phase 1, not deferred.
- Gemini system-level config is out of mutation scope in Phase 1.
  - The Gemini adapter must detect it if present and report it as a higher-precedence or non-writable layer.
- Verification commands must be adapter-specific:
  - Claude: use `claude mcp get <name>` or `claude mcp list`.
  - Codex: use `codex mcp get <name>` or `codex mcp list`.
  - Gemini: use `gemini mcp list` plus settings-file verification for hooks.
- JSON formatting preservation is best-effort.
  - Semantic preservation of unrelated keys is the hard requirement.

## Host Authority Table

These are the authoritative Phase 1 assumptions. Do not leave them as “verify later” during implementation.

### Claude Code

- Host-native MCP mutation:
  - add: `claude mcp add <name> --scope <local|user|project> -- <command> [args...]`
  - remove: `claude mcp remove <name> --scope <local|user|project>`
  - verify: `claude mcp get <name>` or `claude mcp list`
- Scope storage observed in isolated temp homes:
  - `user` -> `~/.claude.json` top-level `mcpServers`
  - `local` -> `~/.claude.json` under `projects["<abs project path>"].mcpServers`
  - `project` -> `.mcp.json` in project root
- Effective-scope rule for Phase 1:
  - detect all three layers
  - if `project` config exists for the same server name, it shadows `user`
  - if `local` config exists for the same project and server name, it shadows both `project` and `user`

### Codex CLI

- Host-native MCP mutation:
  - add: `codex mcp add <name> -- <command> [args...]`
  - remove: `codex mcp remove <name>`
  - verify: `codex mcp get <name>` or `codex mcp list`
- Scope storage observed locally:
  - global config path: `~/.codex/config.toml`
- Effective-scope rule for Phase 1:
  - treat `~/.codex/config.toml` as the writable host-native MCP source of truth
  - also detect repo-local `.codex-plugin/` as a repo-coupled activation signal that may make global setup redundant or confusing
  - report `.codex-plugin/plugin.json` and `.codex-plugin/hooks.json` as project-local shadowing signals, but do not mutate them in Phase 1

### Gemini CLI

- Host-native MCP mutation:
  - add: `gemini mcp add <name> <command> [args...] --scope <user|project>`
  - remove: `gemini mcp remove <name>`
  - verify: `gemini mcp list`
- Settings precedence from official docs:
  1. system defaults
  2. user settings
  3. project settings
  4. system settings
  5. env vars
  6. CLI args
- Settings file paths:
  - user: `~/.gemini/settings.json`
  - project: `.gemini/settings.json`
  - system defaults: `/etc/gemini-cli/system-defaults.json` on Linux, platform equivalents elsewhere
  - system overrides: `/etc/gemini-cli/settings.json` on Linux, platform equivalents elsewhere
- Effective-scope rule for Phase 1:
  - mutate only `user` and `project`
  - detect system defaults and system settings, report them, never write them
- Hook semantics from official docs:
  - `PreCompress` is advisory-only and cannot block compression
  - Phase 1 Gemini integration installs only a `PreCompress` hook
  - Gemini hook output must use `systemMessage` and/or side effects, not `decision: block`

## Task 1: Add Stable Entrypoints And CLI Scaffolding

**Files:**
- Modify: `pyproject.toml`
- Modify: `mempalace/cli.py`
- Create: `mempalace/mcp_main.py`
- Modify: `mempalace/mcp_server.py`
- Modify: `examples/mcp_setup.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_version_consistency.py`

- [ ] **Step 1: Write the failing CLI dispatch tests**

Add tests to `tests/test_cli.py` for:

```python
def test_main_integrate_dispatches():
    ...


def test_main_integrate_remove_dispatches():
    ...
```

Also add a version/packaging test in `tests/test_version_consistency.py` that asserts `pyproject.toml` defines both console scripts and that the wrapper module is invokable:

```python
def test_pyproject_defines_cli_scripts():
    content = pyproject.read_text(encoding="utf-8")
    assert 'mempalace = "mempalace:main"' in content
    assert 'mempalace-mcp = "mempalace.mcp_main:main"' in content


def test_mcp_main_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "mempalace.mcp_main", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--palace" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_cli.py::test_main_integrate_dispatches tests/test_cli.py::test_main_integrate_remove_dispatches tests/test_version_consistency.py::test_pyproject_defines_cli_scripts tests/test_version_consistency.py::test_mcp_main_help_smoke -v
```

Expected:
- CLI tests fail because `integrate` is not defined.
- script test fails because `mempalace-mcp` does not exist.

- [ ] **Step 3: Add the minimal entrypoint implementation**

Create `mempalace/mcp_main.py`:

```python
"""Stable console-script entrypoint for the MemPalace MCP server."""

from .mcp_server import main


if __name__ == "__main__":
    main()
```

Update `pyproject.toml`:

```toml
[project.scripts]
mempalace = "mempalace:main"
mempalace-mcp = "mempalace.mcp_main:main"
```

Add `integrate` and `integrate remove` parser scaffolding in `mempalace/cli.py` that dispatches into a placeholder manager function to be implemented later.

Update immediate user-facing guidance in:

- `mempalace/cli.py` `cmd_mcp`
- `mempalace/mcp_server.py` module help text
- `examples/mcp_setup.md`

to prefer:

```bash
claude mcp add mempalace -- mempalace-mcp
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_cli.py::test_main_integrate_dispatches tests/test_cli.py::test_main_integrate_remove_dispatches tests/test_version_consistency.py -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml mempalace/cli.py mempalace/mcp_main.py mempalace/mcp_server.py examples/mcp_setup.md tests/test_cli.py tests/test_version_consistency.py
git commit -m "feat: add integration CLI scaffolding and mcp entrypoint"
```

## Task 2: Build Shared Integration Models And Atomic IO

**Files:**
- Create: `mempalace/integrations/__init__.py`
- Create: `mempalace/integrations/base.py`
- Create: `mempalace/integrations/io.py`
- Create: `tests/test_integrations_base.py`

- [ ] **Step 1: Write failing tests for the shared data model and IO helpers**

Add tests for:

```python
def test_backup_path_contains_host_and_timestamp(tmp_path):
    ...


def test_atomic_write_replaces_file_only_after_validation(tmp_path):
    ...


def test_atomic_write_keeps_original_when_validator_fails(tmp_path):
    ...


def test_plan_action_tracks_effective_and_requested_scope():
    ...


def test_json_write_preserves_unrelated_keys_semantically(tmp_path):
    ...
```

Use explicit data expectations, not mocks, for backup path naming and file replacement behavior.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_integrations_base.py -v
```

Expected:
- FAIL because shared models and IO helpers do not exist.

- [ ] **Step 3: Implement minimal shared structures**

Create `mempalace/integrations/base.py` with focused dataclasses:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


@dataclass
class IntegrationAction:
    host: str
    kind: str
    status: str
    summary: str
    path: Optional[Path] = None
    requested_scope: str = "auto"
    effective_scope: Optional[str] = None
    shadowed_by: Optional[str] = None
    backup_path: Optional[Path] = None
    use_host_cli: bool = False
```

Create `mempalace/integrations/io.py` with:

- backup path helper
- atomic text write helper
- atomic JSON write helper
- re-parse validator callbacks

Keep helpers small and host-agnostic.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_integrations_base.py -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/integrations/__init__.py mempalace/integrations/base.py mempalace/integrations/io.py tests/test_integrations_base.py
git commit -m "feat: add integration models and atomic io helpers"
```

## Task 3: Implement Manager Planning, Apply, And Remove Flows

**Files:**
- Create: `mempalace/integrations/manager.py`
- Modify: `mempalace/cli.py`
- Create: `tests/test_integrations_manager.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing manager tests**

Add tests for:

```python
def test_autodiscovery_selects_only_detected_hosts(monkeypatch):
    ...


def test_explicit_hosts_override_autodiscovery(monkeypatch):
    ...


def test_dry_run_returns_plan_without_apply(monkeypatch):
    ...


def test_remove_mode_only_targets_mempalace_managed_state(monkeypatch):
    ...


def test_write_mode_skips_prompt(monkeypatch):
    ...


def test_manager_isolates_failures_per_host(monkeypatch):
    ...


def test_rendered_plan_includes_scope_shadowing_and_mutation_mode(monkeypatch, capsys):
    ...


def test_idempotent_reapply_does_not_duplicate_actions(monkeypatch):
    ...


def test_manager_creates_backup_before_first_host_file_write(tmp_path, monkeypatch):
    ...
```

Also add CLI-facing tests that assert the parsed subcommand calls the manager with the right mode and flags.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_integrations_manager.py tests/test_cli.py -k "integrate or remove" -v
```

Expected:
- FAIL because manager orchestration does not exist.

- [ ] **Step 3: Implement minimal manager orchestration**

Create `mempalace/integrations/manager.py` with:

```python
def run_integrations(hosts, dry_run, write, palace, scope, remove):
    adapters = get_adapters()
    selected = select_adapters(adapters, hosts)
    plan = build_plan(selected, palace=palace, scope=scope, remove=remove)
    render_plan(plan)
    if dry_run:
        return 0
    if not write and not _confirm():
        return 1
    return apply_plan(plan)
```

Requirements:
- separate plan generation from application
- no file writes during discovery or rendering
- remove mode must be explicit, not inferred
- plan entries must include requested/effective scope and host CLI vs file patch path
- per-host apply failures must not stop subsequent hosts
- repeated apply with the same effective state must return `skip` or no-op, not duplicate mutations

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_integrations_manager.py tests/test_cli.py -k "integrate or remove" -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/integrations/manager.py mempalace/cli.py tests/test_integrations_manager.py tests/test_cli.py
git commit -m "feat: add integration manager plan apply and remove flows"
```

## Task 4: Implement Codex Adapter With Host-Native MCP Preference

**Files:**
- Create: `mempalace/integrations/codex.py`
- Create: `tests/test_integrations_codex.py`

- [ ] **Step 1: Write failing Codex adapter tests**

Add tests for:

```python
def test_codex_detect_reports_global_and_repo_local_layers(tmp_path, monkeypatch):
    ...


def test_codex_prefers_host_cli_when_codex_binary_exists(monkeypatch):
    ...


def test_codex_reports_shadowing_when_repo_plugin_overrides_user_config(tmp_path):
    ...


def test_codex_remove_targets_only_mempalace_registration(monkeypatch):
    ...


def test_codex_fallback_invalid_toml_reports_cannot_apply(tmp_path):
    ...


def test_codex_reapply_is_idempotent(monkeypatch):
    ...
```

Use temp config files and monkeypatched `shutil.which` / `subprocess.run`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_integrations_codex.py -v
```

Expected:
- FAIL because Codex adapter does not exist.

- [ ] **Step 3: Implement the minimal Codex adapter**

Adapter responsibilities:
- detect Codex CLI presence
- discover global config and repo-local plugin/config signals
- compute effective scope for `auto`
- build MCP add/remove actions using `codex mcp add/remove` when available
- fall back to validated file patching only when the CLI is unavailable and the target file is safe to edit

Keep CLI command construction explicit:

```python
["codex", "mcp", "add", "mempalace", "mempalace-mcp"]
```

Also pin verification:

```python
["codex", "mcp", "get", "mempalace"]
```

Do not guess unsupported flags. Adapter tests must pin the exact command shape.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_integrations_codex.py -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/integrations/codex.py tests/test_integrations_codex.py
git commit -m "feat: add codex integration adapter"
```

## Task 5: Implement Claude Adapter With Effective-Scope Detection

**Files:**
- Create: `mempalace/integrations/claude.py`
- Create: `tests/test_integrations_claude.py`

- [ ] **Step 1: Write failing Claude adapter tests**

Add tests for:

```python
def test_claude_detect_reports_local_project_and_user_layers(tmp_path, monkeypatch):
    ...


def test_claude_prefers_host_cli_when_available(monkeypatch):
    ...


def test_claude_refuses_user_write_when_project_scope_is_effective(tmp_path):
    ...


def test_claude_verify_effective_state_after_apply(monkeypatch):
    ...


def test_claude_project_scope_writes_mcp_json_in_fallback(tmp_path):
    ...


def test_claude_local_scope_maps_to_projects_block_in_claude_json(tmp_path):
    ...


def test_claude_invalid_json_fallback_reports_cannot_apply(tmp_path):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_integrations_claude.py -v
```

Expected:
- FAIL because Claude adapter does not exist.

- [ ] **Step 3: Implement the minimal Claude adapter**

Adapter responsibilities:
- detect available Claude config layers
- select effective writable target under `auto`
- use `claude mcp add/remove` when available
- expose fallback file mutation only for validated supported targets
- verify result with a Claude inspection/list command if exposed; otherwise report fallback-file-only verification

Keep the exact command forms explicit:

```python
["claude", "mcp", "add", "mempalace", "--scope", scope, "--", "mempalace-mcp"]
["claude", "mcp", "get", "mempalace"]
```

Fallback file targets are fixed in Phase 1:
- `user` -> `~/.claude.json`
- `local` -> `~/.claude.json` under `projects[abs_project_path]`
- `project` -> `.mcp.json`

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_integrations_claude.py -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/integrations/claude.py tests/test_integrations_claude.py
git commit -m "feat: add claude integration adapter"
```

## Task 6: Implement Gemini Adapter And Extend Hook Harness

**Files:**
- Create: `mempalace/integrations/gemini.py`
- Modify: `mempalace/hooks_cli.py`
- Create: `tests/test_integrations_gemini.py`
- Modify: `tests/test_hooks_cli.py`

- [ ] **Step 1: Write failing Gemini adapter and hook tests**

Add adapter tests for:

```python
def test_gemini_detect_reports_user_project_and_system_layers(tmp_path, monkeypatch):
    ...


def test_gemini_marks_system_scope_as_detected_but_not_mutated(tmp_path):
    ...


def test_gemini_builds_precompress_hook_command():
    ...


def test_gemini_remove_preserves_unrelated_settings(tmp_path):
    ...


def test_gemini_hook_install_is_project_or_user_only(tmp_path):
    ...


def test_gemini_invalid_settings_reports_cannot_apply(tmp_path):
    ...
```

Add hook tests for:

```python
def test_parse_harness_input_gemini():
    ...


def test_run_hook_precompact_gemini_dispatches():
    ...


def test_run_hook_precompact_gemini_returns_system_message_not_block():
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_integrations_gemini.py tests/test_hooks_cli.py -k "gemini or precompact" -v
```

Expected:
- FAIL because Gemini adapter and harness do not exist.

- [ ] **Step 3: Implement Gemini adapter and harness**

Gemini adapter requirements:
- detect user, project, and system layers
- treat system layer as detectable but not writable in Phase 1
- use `gemini mcp add/remove` when available
- patch hooks structurally in Gemini settings JSON
- preserve unrelated JSON keys semantically

Hook harness requirements in `mempalace/hooks_cli.py`:

```python
SUPPORTED_HARNESSES = {"claude-code", "codex", "gemini"}
```

and explicit Gemini parsing based on official base hook input:

```python
if harness == "gemini":
    return {
        "session_id": _sanitize_session_id(...),
        "transcript_path": str(data.get("transcript_path", "")),
        "cwd": str(data.get("cwd", "")),
        "hook_event_name": str(data.get("hook_event_name", "")),
        "trigger": str(data.get("trigger", "")),
    }
```

Phase 1 Gemini hook behavior is fixed:

- install only `PreCompress`
- use command:

```bash
mempalace hook run --hook precompact --harness gemini
```

- output for Gemini `precompact` must be advisory, e.g.:

```json
{
  "systemMessage": "COMPACTION IMMINENT. Save ALL topics, decisions, quotes, code, and important context from this session to your memory system."
}
```

- do not emit blocking decisions for Gemini `PreCompress`, because Gemini ignores flow-control there

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_integrations_gemini.py tests/test_hooks_cli.py -k "gemini or precompact" -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/integrations/gemini.py mempalace/hooks_cli.py tests/test_integrations_gemini.py tests/test_hooks_cli.py
git commit -m "feat: add gemini integration adapter and hook harness"
```

## Task 7: Tighten CLI Help Surface And End-To-End Verification

**Files:**
- Modify: `mempalace/instructions/help.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_version_consistency.py`

- [ ] **Step 1: Write failing help and smoke tests**

Add tests for:

```python
def test_main_help_mentions_integrate(capsys):
    ...


def test_help_instructions_mentions_integrate_command():
    ...
```

If practical, add one smoke test that imports the manager and confirms all three adapters are registered.

Also update existing `mempalace mcp` tests to expect `mempalace-mcp`, not `python -m mempalace.mcp_server`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_cli.py::test_main_help_mentions_integrate tests/test_cli.py::test_mcp_command_prints_setup_guidance tests/test_cli.py::test_mcp_command_uses_custom_palace_path_when_provided tests/test_version_consistency.py -v
```

Expected:
- FAIL because help text does not mention integration support.

- [ ] **Step 3: Implement minimal help updates**

Update `mempalace/instructions/help.md` and any CLI epilog/help text to mention:

```text
mempalace integrate
mempalace integrate --dry-run
mempalace integrate remove
```

Also update `mempalace mcp` output to use `mempalace-mcp` for the default MCP setup guidance.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
pytest tests/test_integrations_base.py tests/test_integrations_manager.py tests/test_integrations_claude.py tests/test_integrations_codex.py tests/test_integrations_gemini.py tests/test_cli.py tests/test_hooks_cli.py tests/test_version_consistency.py -v
```

Then run broader regression coverage:

```bash
pytest tests/test_cli.py tests/test_hooks_cli.py tests/test_instructions_cli.py tests/test_mcp_server.py -v
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/instructions/help.md tests/test_cli.py tests/test_version_consistency.py
git commit -m "feat: expose integration manager in help and verify adapters"
```

## Implementation Notes

- Keep adapters small and independent.
- Do not introduce a generic “host framework” abstraction beyond what the tests force.
- For direct file patch fallback, preserve semantics first and formatting second.
- If a host-native CLI command is uncertain, verify it against current host docs before coding; do not guess.
- Treat all precedence logic as adapter-owned. Avoid a global precedence engine.

## Final Verification Checklist

- `mempalace-mcp` is present in `pyproject.toml`
- `python -m mempalace.mcp_server` is no longer the primary generated setup guidance
- `mempalace integrate` and `mempalace integrate remove` are visible in CLI help
- manager performs no writes during `--dry-run`
- manager can remove only MemPalace-owned integration state
- Gemini harness is supported by `hooks_cli`
- Gemini `PreCompress` is advisory-only and returns `systemMessage`, not a blocking decision
- host-native MCP mutation is used when available
- fallback file patching is atomic and validated
- fallback parse failures surface `cannot_apply`
- post-apply verification uses host-native inspection commands when available
- per-host failure isolation is covered by tests
- plan output includes requested scope, effective scope, shadowing, and mutation mode
- shadowed config is reported instead of silently misconfiguring the host

## Handoff

Plan assumes a single implementation branch and TDD-first execution. If execution is split across workers, keep write scopes disjoint:

- Worker 1: package scripts + CLI + manager core
- Worker 2: Claude and Codex adapters
- Worker 3: Gemini adapter + `hooks_cli`
