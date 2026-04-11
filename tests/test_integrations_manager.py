from pathlib import Path

import pytest

from mempalace.integrations.base import IntegrationAction
from mempalace.integrations.io import atomic_write_text
from mempalace.integrations.manager import (
    apply_plan,
    build_plan,
    render_plan,
    run_integrations,
    select_adapters,
)


class FakeAdapter:
    def __init__(self, name, *, detected=True, planner=None, apply_impl=None):
        self.name = name
        self.detected = detected
        self.planner = planner
        self.apply_impl = apply_impl
        self.detect_calls = 0
        self.plan_calls = []
        self.apply_calls = []

    def detect(self):
        self.detect_calls += 1
        return self.detected

    def plan(self, *, palace=None, scope="auto", remove=False):
        self.plan_calls.append(
            {
                "palace": palace,
                "scope": scope,
                "remove": remove,
            }
        )
        if callable(self.planner):
            return self.planner(palace=palace, scope=scope, remove=remove)
        return self.planner

    def apply(self, action):
        self.apply_calls.append(action)
        if callable(self.apply_impl):
            return self.apply_impl(action)
        return action


def test_autodiscovery_selects_only_detected_hosts():
    claude = FakeAdapter("claude")
    codex = FakeAdapter("codex", detected=False)

    selected = select_adapters([claude, codex], hosts=[])

    assert selected == [claude]
    assert claude.detect_calls == 1
    assert codex.detect_calls == 1


def test_explicit_hosts_override_autodiscovery():
    claude = FakeAdapter("claude", detected=False)
    codex = FakeAdapter("codex", detected=False)

    selected = select_adapters([claude, codex], hosts=["codex"])

    assert selected == [codex]
    assert claude.detect_calls == 0
    assert codex.detect_calls == 0


def test_run_integrations_rejects_unknown_explicit_hosts(monkeypatch, capsys):
    claude = FakeAdapter("claude")
    codex = FakeAdapter("codex")
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude, codex])

    result = run_integrations(
        hosts=["claud"],
        dry_run=True,
        write=False,
        palace=None,
        scope="auto",
        remove=False,
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "Unknown hosts" in captured.err
    assert "claud" in captured.err
    assert "claude" in captured.err
    assert "codex" in captured.err


def test_dry_run_returns_plan_without_apply(monkeypatch):
    claude = FakeAdapter(
        "claude",
        planner=IntegrationAction(
            host="claude",
            kind="mcp",
            status="create",
            summary="Add MemPalace MCP server",
            use_host_cli=True,
        ),
    )
    rendered = []

    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude])
    monkeypatch.setattr(
        "mempalace.integrations.manager.render_plan",
        lambda plan: rendered.extend(plan),
    )
    monkeypatch.setattr(
        "mempalace.integrations.manager.apply_plan",
        lambda _plan: pytest.fail("dry run should not apply"),
    )

    result = run_integrations(hosts=[], dry_run=True, write=False, palace=None, scope="auto", remove=False)

    assert result == 0
    assert len(rendered) == 1
    assert claude.apply_calls == []


def test_remove_mode_only_targets_mempalace_managed_state(monkeypatch):
    def planner(**kwargs):
        assert kwargs["remove"] is True
        return IntegrationAction(
            host="claude",
            kind="remove",
            status="update",
            summary="Remove MemPalace-managed MCP registration",
            use_host_cli=True,
        )

    claude = FakeAdapter("claude", planner=planner)
    rendered = []
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude])
    monkeypatch.setattr(
        "mempalace.integrations.manager.render_plan",
        lambda plan: rendered.extend(plan),
    )

    result = run_integrations(hosts=[], dry_run=True, write=False, palace=None, scope="user", remove=True)

    assert result == 0
    assert rendered[0]["action"].kind == "remove"
    assert "MemPalace-managed" in rendered[0]["action"].summary


def test_write_mode_skips_prompt(monkeypatch):
    claude = FakeAdapter(
        "claude",
        planner=IntegrationAction(
            host="claude",
            kind="mcp",
            status="create",
            summary="Add MemPalace MCP server",
            use_host_cli=True,
        ),
    )

    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude])
    monkeypatch.setattr(
        "mempalace.integrations.manager._confirm",
        lambda: pytest.fail("write mode should skip confirmation"),
    )

    result = run_integrations(hosts=[], dry_run=False, write=True, palace=None, scope="auto", remove=False)

    assert result == 0
    assert len(claude.apply_calls) == 1


def test_empty_plan_skips_confirmation(monkeypatch):
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [])
    monkeypatch.setattr(
        "mempalace.integrations.manager._confirm",
        lambda: pytest.fail("empty plans should not prompt"),
    )

    result = run_integrations(hosts=[], dry_run=False, write=False, palace=None, scope="auto", remove=False)

    assert result == 0


def test_manager_isolates_failures_per_host(monkeypatch):
    failing = FakeAdapter(
        "claude",
        planner=IntegrationAction(
            host="claude",
            kind="mcp",
            status="create",
            summary="Add MemPalace MCP server",
            use_host_cli=True,
        ),
        apply_impl=lambda _action: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    succeeding = FakeAdapter(
        "codex",
        planner=IntegrationAction(
            host="codex",
            kind="mcp",
            status="create",
            summary="Add MemPalace MCP server",
            use_host_cli=True,
        ),
    )
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [failing, succeeding])

    result = run_integrations(hosts=[], dry_run=False, write=True, palace=None, scope="auto", remove=False)

    assert result == 1
    assert len(failing.apply_calls) == 1
    assert len(succeeding.apply_calls) == 1


def test_rendered_plan_includes_scope_shadowing_and_mutation_mode(capsys):
    render_plan(
        [
            {
                "adapter": object(),
                "action": IntegrationAction(
                    host="claude",
                    kind="mcp",
                    status="update",
                    summary="Update MemPalace MCP registration",
                    requested_scope="auto",
                    effective_scope="project",
                    shadowed_by="workspace",
                    use_host_cli=True,
                ),
            },
            {
                "adapter": object(),
                "action": IntegrationAction(
                    host="gemini",
                    kind="hook",
                    status="update",
                    summary="Write advisory hook settings",
                    requested_scope="user",
                    effective_scope="user",
                    path=Path("/tmp/settings.json"),
                ),
            },
        ]
    )

    out = capsys.readouterr().out
    assert "requested=auto effective=project" in out
    assert "shadowed-by=workspace" in out
    assert "mutation=host-cli" in out
    assert "mutation=file-patch" in out


def test_idempotent_reapply_does_not_duplicate_actions(monkeypatch):
    state = {"applied": False}

    def planner(**_kwargs):
        if state["applied"]:
            return IntegrationAction(
                host="claude",
                kind="mcp",
                status="skip",
                summary="MemPalace MCP registration already present",
                use_host_cli=True,
            )
        return IntegrationAction(
            host="claude",
            kind="mcp",
            status="create",
            summary="Add MemPalace MCP server",
            use_host_cli=True,
        )

    def apply_impl(action):
        state["applied"] = True
        return action

    claude = FakeAdapter("claude", planner=planner, apply_impl=apply_impl)
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude])

    assert run_integrations(hosts=[], dry_run=False, write=True, palace=None, scope="auto", remove=False) == 0
    assert run_integrations(hosts=[], dry_run=False, write=True, palace=None, scope="auto", remove=False) == 0
    assert len(claude.apply_calls) == 1


def test_manager_creates_backup_before_first_host_file_write(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    expected_backup = tmp_path / "settings.claude.20260410T120000Z.bak.json"

    def planner(**_kwargs):
        return IntegrationAction(
            host="claude",
            kind="mcp",
            status="update",
            summary="Update MemPalace MCP registration",
            path=target,
        )

    def apply_impl(action):
        backup_path = atomic_write_text(
            action.path,
            '{"new": true}\n',
            host=action.host,
            timestamp="20260410T120000Z",
        )
        assert backup_path == expected_backup
        return IntegrationAction(
            host=action.host,
            kind=action.kind,
            status=action.status,
            summary=action.summary,
            path=action.path,
            backup_path=backup_path,
        )

    claude = FakeAdapter("claude", planner=planner, apply_impl=apply_impl)
    monkeypatch.setattr("mempalace.integrations.manager.get_adapters", lambda: [claude])

    assert run_integrations(hosts=[], dry_run=False, write=True, palace=None, scope="auto", remove=False) == 0
    assert target.read_text(encoding="utf-8") == '{"new": true}\n'
    assert expected_backup.read_text(encoding="utf-8") == '{"old": true}\n'
