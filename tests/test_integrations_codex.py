import json
from subprocess import CompletedProcess

import pytest

from mempalace.integrations.codex import CodexAdapter


def _codex_hook_command(hook_name: str) -> str:
    return f"mempalace hook run --hook {hook_name} --harness codex"


def _codex_hook_group(hook_name: str) -> dict[str, object]:
    return {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": _codex_hook_command(hook_name),
            }
        ],
    }


def _action_by_kind(actions, kind: str):
    return next(action for action in actions if action.kind == kind)


def test_codex_detect_reports_global_and_repo_local_layers(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    plugin_json = project_root / ".codex-plugin" / "plugin.json"
    user_config.parent.mkdir(parents=True)
    plugin_json.parent.mkdir(parents=True)
    user_config.write_text('[mcp_servers.other]\ncommand = "other"\n', encoding="utf-8")
    plugin_json.write_text(
        json.dumps({"mcpServers": {"mempalace": {"command": "mempalace-mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    layers = adapter.discover()

    assert adapter.detect() is True
    assert layers["cli_available"] is False
    assert layers["user_config_path"] == user_config
    assert layers["user_config_exists"] is True
    assert layers["repo_plugin_path"] == plugin_json
    assert layers["repo_plugin_has_mempalace"] is True


def test_codex_plan_includes_hook_action(tmp_path, monkeypatch):
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=tmp_path / "home", project_root=tmp_path / "repo")
    actions = adapter.plan(palace=None, scope="auto", remove=False)

    assert isinstance(actions, list)
    assert [action.kind for action in actions] == ["mcp", "hook"]
    assert actions[1].path == tmp_path / "home" / ".codex" / "hooks.json"


def test_codex_hook_fallback_writes_supported_events(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    actions = adapter.plan(palace=None, scope="auto", remove=False)
    hook_action = next(action for action in actions if action.kind == "hook")
    adapter.apply(hook_action)

    hooks_path = home_dir / ".codex" / "hooks.json"
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert payload["hooks"]["SessionStart"] == [_codex_hook_group("session-start")]
    assert payload["hooks"]["Stop"] == [_codex_hook_group("stop")]
    assert "PreCompact" not in payload["hooks"]


def test_codex_hook_shadowing_reports_repo_plugin_hooks(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    plugin_hooks = project_root / ".codex-plugin" / "hooks.json"
    plugin_hooks.parent.mkdir(parents=True)
    plugin_hooks.write_text(
        json.dumps({"hooks": {"SessionStart": [_codex_hook_group("session-start")]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    actions = adapter.plan(palace=None, scope="auto", remove=False)
    hook_action = next(action for action in actions if action.kind == "hook")

    assert hook_action.status == "cannot_apply"
    assert hook_action.shadowed_by == "repo-plugin"


def test_codex_hook_plan_rejects_non_list_target_event_shapes(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    hooks_path = home_dir / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"hooks": {"SessionStart": {"matcher": "*"}}}), encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=tmp_path / "repo")
    actions = adapter.plan(palace=None, scope="auto", remove=False)
    hook_action = next(action for action in actions if action.kind == "hook")

    assert hook_action.status == "cannot_apply"
    assert hook_action.summary == "Codex user hooks shape is unsupported for fallback write"


def test_codex_hook_apply_refuses_invalid_json_after_plan(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=tmp_path / "repo")
    actions = adapter.plan(palace=None, scope="auto", remove=False)
    hook_action = next(action for action in actions if action.kind == "hook")
    hook_action.path.parent.mkdir(parents=True)
    hook_action.path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        adapter.apply(hook_action)


def test_codex_prefers_host_cli_when_codex_binary_exists(tmp_path, monkeypatch):
    commands = []

    def fake_run(argv, **kwargs):
        commands.append((argv, kwargs))
        if argv[:3] == ["codex", "mcp", "get"]:
            return CompletedProcess(argv, 0, stdout="mempalace\n", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr("mempalace.integrations.codex.subprocess.run", fake_run)

    adapter = CodexAdapter(home_dir=tmp_path / "home", project_root=tmp_path / "repo")
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=False), "mcp")
    result = adapter.apply(action)

    assert action.use_host_cli is True
    assert action.status == "create"
    assert commands[0][0] == ["codex", "mcp", "add", "mempalace", "--", "mempalace-mcp"]
    assert commands[1][0] == ["codex", "mcp", "get", "mempalace"]
    assert result.use_host_cli is True


def test_codex_cli_add_passes_custom_palace_args(tmp_path, monkeypatch):
    commands = []

    def fake_run(argv, **kwargs):
        commands.append((argv, kwargs))
        if argv[:3] == ["codex", "mcp", "get"]:
            return CompletedProcess(argv, 0, stdout="mempalace\n", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr("mempalace.integrations.codex.subprocess.run", fake_run)

    palace = tmp_path / "palace"
    adapter = CodexAdapter(home_dir=tmp_path / "home", project_root=tmp_path / "repo")
    action = _action_by_kind(adapter.plan(palace=palace, scope="auto", remove=False), "mcp")
    adapter.apply(action)

    assert commands[0][0] == [
        "codex",
        "mcp",
        "add",
        "mempalace",
        "--",
        "mempalace-mcp",
        "--palace",
        str(palace),
    ]


def test_codex_reports_shadowing_when_repo_plugin_overrides_user_config(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    plugin_json = project_root / ".codex-plugin" / "plugin.json"
    user_config.parent.mkdir(parents=True)
    plugin_json.parent.mkdir(parents=True)
    user_config.write_text("", encoding="utf-8")
    plugin_json.write_text(
        json.dumps({"mcpServers": {"mempalace": {"command": "mempalace-mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: "/usr/bin/codex")

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=False), "mcp")

    assert action.status == "cannot_apply"
    assert action.effective_scope == "repo-plugin"
    assert action.shadowed_by == "repo-plugin"


def test_codex_user_scope_respects_repo_plugin_shadowing(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    plugin_json = project_root / ".codex-plugin" / "plugin.json"
    user_config.parent.mkdir(parents=True)
    plugin_json.parent.mkdir(parents=True)
    user_config.write_text("", encoding="utf-8")
    plugin_json.write_text(
        json.dumps({"mcpServers": {"mempalace": {"command": "mempalace-mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: "/usr/bin/codex")

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="user", remove=False), "mcp")

    assert action.status == "cannot_apply"
    assert action.effective_scope == "repo-plugin"
    assert action.shadowed_by == "repo-plugin"


def test_codex_remove_respects_repo_plugin_shadowing(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    plugin_json = project_root / ".codex-plugin" / "plugin.json"
    user_config.parent.mkdir(parents=True)
    plugin_json.parent.mkdir(parents=True)
    user_config.write_text("", encoding="utf-8")
    plugin_json.write_text(
        json.dumps({"mcpServers": {"mempalace": {"command": "mempalace-mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: "/usr/bin/codex")

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="user", remove=True), "remove")

    assert action.status == "cannot_apply"
    assert action.effective_scope == "repo-plugin"
    assert action.shadowed_by == "repo-plugin"


def test_codex_remove_targets_only_mempalace_registration(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        (
            '[mcp_servers.other]\n'
            'command = "other"\n'
            '\n'
            '[mcp_servers.mempalace]\n'
            'command = "mempalace-mcp"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=True), "remove")
    adapter.apply(action)

    content = user_config.read_text(encoding="utf-8")
    assert "[mcp_servers.other]" in content
    assert "[mcp_servers.mempalace]" not in content


def test_codex_fallback_invalid_toml_reports_cannot_apply(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[mcp_servers\ncommand = 'oops'\n", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=False), "mcp")

    assert action.status == "cannot_apply"
    assert action.use_host_cli is False
    assert action.path == user_config


def test_codex_reapply_is_idempotent(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        (
            '[mcp_servers.mempalace]\n'
            'command = "mempalace-mcp"\n'
            'args = []\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=False), "mcp")

    assert action.status == "skip"
    assert "already present" in action.summary


def test_codex_fallback_writes_custom_palace_args(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    palace = tmp_path / "palace"
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=palace, scope="auto", remove=False), "mcp")
    adapter.apply(action)

    content = user_config.read_text(encoding="utf-8")
    assert 'command = "mempalace-mcp"' in content
    assert f'args = ["--palace", "{palace}"]' in content


def test_codex_remove_matches_section_header_with_inline_comment(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        (
            '[mcp_servers.mempalace]  # managed by mempalace\n'
            'command = "mempalace-mcp"\n'
            'args = []\n'
            '\n'
            '[mcp_servers.other]\n'
            'command = "other"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=True), "remove")
    adapter.apply(action)

    content = user_config.read_text(encoding="utf-8")
    assert "[mcp_servers.mempalace]" not in content
    assert '[mcp_servers.other]\ncommand = "other"' in content


def test_codex_fallback_verifies_remove_result(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        (
            '[mcp_servers.mempalace]\n'
            'command = "mempalace-mcp"\n'
            'args = []\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    def fake_atomic_write_text(path, content, **kwargs):
        path.write_text(
            (
                '[mcp_servers.mempalace]\n'
                'command = "mempalace-mcp"\n'
                'args = []\n'
            ),
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("mempalace.integrations.codex.atomic_write_text", fake_atomic_write_text)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = _action_by_kind(adapter.plan(palace=None, scope="auto", remove=True), "remove")

    with pytest.raises(RuntimeError, match="still contains mempalace after remove"):
        adapter.apply(action)
