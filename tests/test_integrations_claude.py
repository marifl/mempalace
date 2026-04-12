import json
from pathlib import Path
from subprocess import CompletedProcess

from mempalace.integrations.claude import ClaudeAdapter


def _claude_server_entry():
    return {"type": "stdio", "command": "mempalace-mcp", "args": [], "env": {}}


def _claude_hook_command(hook: str):
    return f"mempalace hook run --hook {hook} --harness claude-code"


def _claude_hook_handler(event: str):
    hook_name = {
        "SessionStart": "session-start",
        "Stop": "stop",
        "PreCompact": "precompact",
    }[event]
    return {
        "type": "command",
        "name": f"mempalace-{hook_name}",
        "command": _claude_hook_command(hook_name),
        "description": f"MemPalace {event} hook",
    }


def _claude_hook_group(event: str):
    return {"hooks": [_claude_hook_handler(event)]}


def _mcp_action(actions):
    if not isinstance(actions, list):
        return actions
    return next(action for action in actions if action.kind != "hook")


def _hook_action(actions):
    if not isinstance(actions, list):
        raise AssertionError("expected hook action list")
    return next(action for action in actions if action.kind == "hook")


def _seed_claude_state(
    home_dir: Path,
    project_root: Path,
    *,
    user: bool = False,
    project: bool = False,
    local: bool = False,
    user_hooks: bool = False,
    project_hooks: bool = False,
    local_hooks: bool = False,
) -> Path:
    home_dir.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    payload = {}
    if user:
        payload["mcpServers"] = {"mempalace": _claude_server_entry()}
    if local:
        payload["projects"] = {
            str(project_root.resolve()): {"mcpServers": {"mempalace": _claude_server_entry()}}
        }
    if payload:
        (home_dir / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")
    if project:
        (project_root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"mempalace": _claude_server_entry()}}),
            encoding="utf-8",
        )
    if user_hooks:
        settings_path = home_dir / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"hooks": {"Stop": [_claude_hook_group("Stop")]}}),
            encoding="utf-8",
        )
    if project_hooks:
        settings_path = project_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"hooks": {"PreCompact": [_claude_hook_group("PreCompact")]}}),
            encoding="utf-8",
        )
    if local_hooks:
        settings_path = project_root / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"hooks": {"SessionStart": [_claude_hook_group("SessionStart")]}}),
            encoding="utf-8",
        )
    return project_root


def test_claude_detect_reports_local_project_and_user_layers(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    _seed_claude_state(
        home_dir,
        project_root,
        user=True,
        project=True,
        local=True,
        user_hooks=True,
        project_hooks=True,
        local_hooks=True,
    )
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    layers = adapter.discover()

    assert adapter.detect() is True
    assert layers["cli_available"] is False
    assert layers["user_config_exists"] is True
    assert layers["project_config_exists"] is True
    assert layers["local_config_exists"] is True
    assert layers["user_has_mempalace"] is True
    assert layers["project_has_mempalace"] is True
    assert layers["local_has_mempalace"] is True
    assert layers["user_settings_exists"] is True
    assert layers["project_settings_exists"] is True
    assert layers["local_settings_exists"] is True
    assert layers["user_has_mempalace_hooks"] is True
    assert layers["project_has_mempalace_hooks"] is True
    assert layers["local_has_mempalace_hooks"] is True


def test_claude_auto_prefers_highest_existing_effective_scope(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    user_only_root = _seed_claude_state(
        home_dir / "user-only",
        project_root / "user-only",
        user=True,
        user_hooks=True,
    )
    project_only_root = _seed_claude_state(
        home_dir / "project-only",
        project_root / "project-only",
        user=True,
        project=True,
        project_hooks=True,
    )
    local_root = _seed_claude_state(
        home_dir / "local",
        project_root / "local",
        user=True,
        project=True,
        local=True,
        local_hooks=True,
    )

    user_adapter = ClaudeAdapter(home_dir=home_dir / "user-only", project_root=user_only_root)
    project_adapter = ClaudeAdapter(
        home_dir=home_dir / "project-only",
        project_root=project_only_root,
    )
    local_adapter = ClaudeAdapter(home_dir=home_dir / "local", project_root=local_root)

    assert _mcp_action(user_adapter.plan(scope="auto", remove=False)).effective_scope == "user"
    assert _hook_action(user_adapter.plan(scope="auto", remove=False)).effective_scope == "user"
    assert _mcp_action(project_adapter.plan(scope="auto", remove=False)).effective_scope == "project"
    assert _hook_action(project_adapter.plan(scope="auto", remove=False)).effective_scope == "project"
    assert _mcp_action(local_adapter.plan(scope="auto", remove=False)).effective_scope == "local"
    assert _hook_action(local_adapter.plan(scope="auto", remove=False)).effective_scope == "local"


def test_claude_auto_uses_existing_empty_project_layer_before_user(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".mcp.json").write_text("{}", encoding="utf-8")
    settings_path = project_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    mcp_action = _mcp_action(adapter.plan(scope="auto", remove=False))
    hook_action = _hook_action(adapter.plan(scope="auto", remove=False))

    assert mcp_action.status == "create"
    assert mcp_action.effective_scope == "project"
    assert mcp_action.path == project_root / ".mcp.json"
    assert hook_action.status == "create"
    assert hook_action.effective_scope == "project"
    assert hook_action.path == settings_path


def test_claude_auto_uses_existing_empty_local_layer_before_user(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    home_dir.mkdir(parents=True, exist_ok=True)
    payload = {"projects": {str(project_root.resolve()): {}}}
    (home_dir / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")
    settings_path = project_root / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    mcp_action = _mcp_action(adapter.plan(scope="auto", remove=False))
    hook_action = _hook_action(adapter.plan(scope="auto", remove=False))

    assert mcp_action.status == "create"
    assert mcp_action.effective_scope == "local"
    assert mcp_action.path == home_dir / ".claude.json"
    assert hook_action.status == "create"
    assert hook_action.effective_scope == "local"
    assert hook_action.path == settings_path


def test_claude_auto_falls_through_unsupported_project_layer(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".mcp.json").write_text('{"mcpServers": []}', encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="auto", remove=False))

    assert action.status == "create"
    assert action.effective_scope == "user"
    assert action.path == home_dir / ".claude.json"


def test_claude_rejects_unsupported_json_shape_for_fallback(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    home_dir.mkdir(parents=True)
    (home_dir / ".claude.json").write_text('{"mcpServers": []}', encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="user", remove=False))

    assert action.status == "cannot_apply"
    assert action.path == home_dir / ".claude.json"


def test_claude_hook_rejects_unsupported_settings_shape_for_fallback(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    settings_path = home_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"hooks": []}', encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _hook_action(adapter.plan(scope="user", remove=False))

    assert action.status == "cannot_apply"
    assert action.path == settings_path


def test_claude_fallback_verifies_written_registration(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    def fake_atomic_write_text(path, content, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"mcpServers": {}}', encoding="utf-8")
        return None

    monkeypatch.setattr("mempalace.integrations.claude.atomic_write_text", fake_atomic_write_text)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="project", remove=False))

    try:
        adapter.apply(action)
    except RuntimeError as exc:
        assert "did not verify" in str(exc) or "Claude" in str(exc)
    else:
        raise AssertionError("fallback write should verify the resulting registration")


def test_claude_prefers_host_cli_for_mcp_but_file_patch_for_hooks(monkeypatch, tmp_path):
    commands = []
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    user_config_path = home_dir / ".claude.json"
    project_settings_path = project_root / ".claude" / "settings.json"

    def fake_run(argv, **kwargs):
        commands.append(argv)
        if argv[:5] == ["claude", "mcp", "add", "mempalace", "--scope"]:
            user_config_path.parent.mkdir(parents=True, exist_ok=True)
            user_config_path.write_text(
                json.dumps({"mcpServers": {"mempalace": _claude_server_entry()}}),
                encoding="utf-8",
            )
            return CompletedProcess(argv, 0, stdout="", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("mempalace.integrations.claude.subprocess.run", fake_run)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    mcp_action = _mcp_action(adapter.plan(scope="user", remove=False))
    hook_action = _hook_action(adapter.plan(scope="project", remove=False))
    mcp_result = adapter.apply(mcp_action)
    hook_result = adapter.apply(hook_action)

    assert mcp_action.use_host_cli is True
    assert hook_action.use_host_cli is False
    assert commands[0] == ["claude", "mcp", "add", "mempalace", "--scope", "user", "--", "mempalace-mcp"]
    payload = json.loads(project_settings_path.read_text(encoding="utf-8"))
    assert payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] == _claude_hook_command("session-start")
    assert payload["hooks"]["Stop"][0]["hooks"][0]["command"] == _claude_hook_command("stop")
    assert payload["hooks"]["PreCompact"][0]["hooks"][0]["command"] == _claude_hook_command("precompact")
    assert mcp_result.status == "skip"
    assert hook_result.status == "skip"


def test_claude_refuses_user_write_when_project_scope_is_effective(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    _seed_claude_state(home_dir, project_root, project=True)
    project_settings_path = project_root / ".claude" / "settings.json"
    project_settings_path.parent.mkdir(parents=True, exist_ok=True)
    project_settings_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    mcp_action = _mcp_action(adapter.plan(scope="user", remove=False))
    hook_action = _hook_action(adapter.plan(scope="user", remove=False))

    assert mcp_action.status == "cannot_apply"
    assert mcp_action.effective_scope == "project"
    assert mcp_action.shadowed_by == "project"
    assert hook_action.status == "cannot_apply"
    assert hook_action.effective_scope == "project"
    assert hook_action.shadowed_by == "project"


def test_claude_verify_effective_state_after_apply(monkeypatch, tmp_path):
    commands = []
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"

    def fake_run(argv, **kwargs):
        commands.append(argv)
        if argv[:5] == ["claude", "mcp", "add", "mempalace", "--scope"]:
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"mempalace": _claude_server_entry()}}),
                encoding="utf-8",
            )
            return CompletedProcess(argv, 0, stdout="", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("mempalace.integrations.claude.subprocess.run", fake_run)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="project", remove=False))
    result = adapter.apply(action)

    assert commands[0] == ["claude", "mcp", "add", "mempalace", "--scope", "project", "--", "mempalace-mcp"]
    assert len(commands) == 1
    assert result.status == "skip"
    assert "present" in result.summary


def test_claude_cli_remove_verifies_target_layer_only(monkeypatch, tmp_path):
    commands = []
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    _seed_claude_state(home_dir, project_root, user=True, project=True)
    project_config = project_root / ".mcp.json"

    def fake_run(argv, **kwargs):
        commands.append(argv)
        if argv[:4] == ["claude", "mcp", "remove", "mempalace"]:
            project_config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            return CompletedProcess(argv, 0, stdout="", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("mempalace.integrations.claude.subprocess.run", fake_run)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="project", remove=True))
    result = adapter.apply(action)

    assert commands[0] == ["claude", "mcp", "remove", "mempalace", "--scope", "project"]
    assert len(commands) == 1
    assert result.status == "skip"
    assert result.summary == "Removed MemPalace MCP registration"
    assert json.loads(project_config.read_text(encoding="utf-8"))["mcpServers"] == {}
    assert json.loads((home_dir / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["mempalace"]


def test_claude_project_scope_writes_mcp_json_in_fallback(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="project", remove=False))
    result = adapter.apply(action)

    payload = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    entry = payload["mcpServers"]["mempalace"]

    assert action.status == "create"
    assert result.backup_path is None
    assert entry == _claude_server_entry()


def test_claude_project_scope_writes_native_hook_settings_in_fallback(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _hook_action(adapter.plan(scope="project", remove=False))
    result = adapter.apply(action)

    payload = json.loads((project_root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    assert action.status == "create"
    assert result.backup_path is None
    assert payload["hooks"]["SessionStart"] == [_claude_hook_group("SessionStart")]
    assert payload["hooks"]["Stop"] == [_claude_hook_group("Stop")]
    assert payload["hooks"]["PreCompact"] == [_claude_hook_group("PreCompact")]


def test_claude_local_scope_maps_to_projects_block_in_claude_json(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="local", remove=False))
    adapter.apply(action)

    payload = json.loads((home_dir / ".claude.json").read_text(encoding="utf-8"))
    entry = payload["projects"][str(project_root.resolve())]["mcpServers"]["mempalace"]

    assert action.status == "create"
    assert entry == _claude_server_entry()


def test_claude_local_scope_writes_settings_local_json_for_hooks(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _hook_action(adapter.plan(scope="local", remove=False))
    adapter.apply(action)

    payload = json.loads((project_root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    assert action.status == "create"
    assert payload["hooks"]["SessionStart"] == [_claude_hook_group("SessionStart")]
    assert payload["hooks"]["Stop"] == [_claude_hook_group("Stop")]
    assert payload["hooks"]["PreCompact"] == [_claude_hook_group("PreCompact")]


def test_claude_hook_remove_preserves_unrelated_settings(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    settings_path = home_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {"FOO": "bar"},
                "hooks": {
                    "Notification": [
                        {"hooks": [{"type": "command", "command": "echo keep", "name": "keep"}]}
                    ],
                    "Stop": [
                        _claude_hook_group("Stop"),
                        {"hooks": [{"type": "command", "command": "echo stop", "name": "other-stop"}]},
                    ],
                    "PreCompact": [
                        _claude_hook_group("PreCompact"),
                    ],
                    "SessionStart": [
                        _claude_hook_group("SessionStart"),
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _hook_action(adapter.plan(scope="user", remove=True))
    result = adapter.apply(action)

    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert result.summary == "Removed MemPalace hooks"
    assert payload["env"]["FOO"] == "bar"
    assert payload["hooks"]["Notification"][0]["hooks"][0]["command"] == "echo keep"
    assert payload["hooks"]["Stop"] == [
        {"hooks": [{"type": "command", "command": "echo stop", "name": "other-stop"}]}
    ]
    assert "PreCompact" not in payload["hooks"]
    assert "SessionStart" not in payload["hooks"]


def test_claude_invalid_json_fallback_reports_cannot_apply(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    home_dir.mkdir(parents=True)
    (home_dir / ".claude.json").write_text("{\"mcpServers\":", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = _mcp_action(adapter.plan(scope="user", remove=False))

    assert action.status == "cannot_apply"
    assert action.path == home_dir / ".claude.json"
