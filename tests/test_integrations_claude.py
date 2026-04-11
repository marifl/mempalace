import json
from pathlib import Path
from subprocess import CompletedProcess

from mempalace.integrations.claude import ClaudeAdapter


def _claude_server_entry():
    return {"type": "stdio", "command": "mempalace-mcp", "args": [], "env": {}}


def _seed_claude_state(
    home_dir: Path,
    project_root: Path,
    *,
    user: bool = False,
    project: bool = False,
    local: bool = False,
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
    return project_root


def test_claude_detect_reports_local_project_and_user_layers(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    _seed_claude_state(home_dir, project_root, user=True, project=True, local=True)
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


def test_claude_auto_prefers_highest_existing_effective_scope(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    user_only_root = _seed_claude_state(home_dir / "user-only", project_root / "user-only", user=True)
    project_only_root = _seed_claude_state(
        home_dir / "project-only",
        project_root / "project-only",
        user=True,
        project=True,
    )
    local_root = _seed_claude_state(
        home_dir / "local",
        project_root / "local",
        user=True,
        project=True,
        local=True,
    )

    user_adapter = ClaudeAdapter(home_dir=home_dir / "user-only", project_root=user_only_root)
    project_adapter = ClaudeAdapter(
        home_dir=home_dir / "project-only",
        project_root=project_only_root,
    )
    local_adapter = ClaudeAdapter(home_dir=home_dir / "local", project_root=local_root)

    assert user_adapter.plan(scope="auto", remove=False).effective_scope == "user"
    assert project_adapter.plan(scope="auto", remove=False).effective_scope == "project"
    assert local_adapter.plan(scope="auto", remove=False).effective_scope == "local"


def test_claude_auto_uses_existing_empty_project_layer_before_user(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="auto", remove=False)

    assert action.status == "create"
    assert action.effective_scope == "project"
    assert action.path == project_root / ".mcp.json"


def test_claude_auto_uses_existing_empty_local_layer_before_user(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    home_dir.mkdir(parents=True, exist_ok=True)
    payload = {"projects": {str(project_root.resolve()): {}}}
    (home_dir / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="auto", remove=False)

    assert action.status == "create"
    assert action.effective_scope == "local"
    assert action.path == home_dir / ".claude.json"


def test_claude_auto_falls_through_unsupported_project_layer(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".mcp.json").write_text('{"mcpServers": []}', encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="auto", remove=False)

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
    action = adapter.plan(scope="user", remove=False)

    assert action.status == "cannot_apply"
    assert action.path == home_dir / ".claude.json"


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
    action = adapter.plan(scope="project", remove=False)

    try:
        adapter.apply(action)
    except RuntimeError as exc:
        assert "did not verify" in str(exc) or "Claude" in str(exc)
    else:
        raise AssertionError("fallback write should verify the resulting registration")


def test_claude_prefers_host_cli_when_available(monkeypatch, tmp_path):
    commands = []
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"

    def fake_run(argv, **kwargs):
        commands.append(argv)
        if argv[:5] == ["claude", "mcp", "add", "mempalace", "--scope"]:
            home_dir.mkdir(parents=True, exist_ok=True)
            (home_dir / ".claude.json").write_text(
                json.dumps({"mcpServers": {"mempalace": _claude_server_entry()}}),
                encoding="utf-8",
            )
            return CompletedProcess(argv, 0, stdout="", stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("mempalace.integrations.claude.subprocess.run", fake_run)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="user", remove=False)
    result = adapter.apply(action)

    assert action.use_host_cli is True
    assert commands[0] == ["claude", "mcp", "add", "mempalace", "--scope", "user", "--", "mempalace-mcp"]
    assert len(commands) == 1
    assert result.status == "skip"


def test_claude_refuses_user_write_when_project_scope_is_effective(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    _seed_claude_state(home_dir, project_root, project=True)
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="user", remove=False)

    assert action.status == "cannot_apply"
    assert action.effective_scope == "project"
    assert action.shadowed_by == "project"


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
    action = adapter.plan(scope="project", remove=False)
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
    action = adapter.plan(scope="project", remove=True)
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
    action = adapter.plan(scope="project", remove=False)
    result = adapter.apply(action)

    payload = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    entry = payload["mcpServers"]["mempalace"]

    assert action.status == "create"
    assert result.backup_path is None
    assert entry == _claude_server_entry()


def test_claude_local_scope_maps_to_projects_block_in_claude_json(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="local", remove=False)
    adapter.apply(action)

    payload = json.loads((home_dir / ".claude.json").read_text(encoding="utf-8"))
    entry = payload["projects"][str(project_root.resolve())]["mcpServers"]["mempalace"]

    assert action.status == "create"
    assert entry == _claude_server_entry()


def test_claude_invalid_json_fallback_reports_cannot_apply(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    home_dir.mkdir(parents=True)
    (home_dir / ".claude.json").write_text("{\"mcpServers\":", encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.claude.shutil.which", lambda _name: None)

    adapter = ClaudeAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(scope="user", remove=False)

    assert action.status == "cannot_apply"
    assert action.path == home_dir / ".claude.json"
