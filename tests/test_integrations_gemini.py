import json
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess

from mempalace.integrations.gemini import GeminiAdapter


def _gemini_server_entry(*args: str):
    return {"command": "mempalace-mcp", "args": list(args)}


def _gemini_hook_command():
    return "mempalace hook run --hook precompact --harness gemini"


def _gemini_hook_definition():
    return {
        "hooks": [
            {
                "type": "command",
                "name": "mempalace-precompress",
                "command": _gemini_hook_command(),
                "description": "Save MemPalace context before compression",
            }
        ]
    }


def _write_settings(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_gemini_detect_reports_user_project_and_system_layers(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    system_defaults = tmp_path / "etc" / "gemini-cli" / "system-defaults.json"
    system_settings = tmp_path / "etc" / "gemini-cli" / "settings.json"

    _write_settings(home_dir / ".gemini" / "settings.json", {"mcpServers": {"other": {"command": "x"}}})
    _write_settings(project_root / ".gemini" / "settings.json", {"hooks": {"PreCompress": [_gemini_hook_definition()]}})
    _write_settings(system_settings, {"mcpServers": {"mempalace": _gemini_server_entry()}})
    _write_settings(system_defaults, {"general": {"preferredEditor": "vim"}})
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)

    adapter = GeminiAdapter(
        home_dir=home_dir,
        project_root=project_root,
        system_defaults_path=system_defaults,
        system_settings_path=system_settings,
    )
    layers = adapter.discover()

    assert adapter.detect() is True
    assert layers["cli_available"] is False
    assert layers["user_config_exists"] is True
    assert layers["project_config_exists"] is True
    assert layers["system_defaults_exists"] is True
    assert layers["system_settings_exists"] is True
    assert layers["project_has_precompress_hook"] is True
    assert layers["system_has_mempalace"] is True


def test_gemini_marks_system_scope_as_detected_but_not_mutated(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    system_settings = tmp_path / "etc" / "gemini-cli" / "settings.json"
    _write_settings(
        system_settings,
        {
            "mcpServers": {"mempalace": _gemini_server_entry()},
            "hooks": {"PreCompress": [_gemini_hook_definition()]},
        },
    )
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)

    adapter = GeminiAdapter(
        home_dir=home_dir,
        project_root=project_root,
        system_settings_path=system_settings,
    )
    actions = adapter.plan(scope="auto", remove=False)
    mcp_action, hook_action = actions

    assert mcp_action.status == "skip"
    assert mcp_action.effective_scope == "system"
    assert mcp_action.shadowed_by == "system"
    assert hook_action.status == "skip"
    assert hook_action.effective_scope == "system"
    assert hook_action.shadowed_by == "system"


def test_gemini_builds_precompress_hook_command(tmp_path):
    adapter = GeminiAdapter(home_dir=tmp_path / "home", project_root=tmp_path / "project")
    assert adapter._build_hook_command() == _gemini_hook_command()


def test_gemini_remove_preserves_unrelated_settings(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    user_settings = home_dir / ".gemini" / "settings.json"
    _write_settings(
        user_settings,
        {
            "general": {"preferredEditor": "vim"},
            "mcpServers": {
                "other": {"command": "other-mcp", "args": []},
                "mempalace": _gemini_server_entry(),
            },
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "echo start", "name": "other"}]}
                ],
                "PreCompress": [
                    _gemini_hook_definition(),
                    {"hooks": [{"type": "command", "command": "echo keep", "name": "keep"}]},
                ],
            },
        },
    )
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)

    adapter = GeminiAdapter(home_dir=home_dir, project_root=project_root)
    actions = adapter.plan(scope="user", remove=True)

    for action in actions:
        adapter.apply(action)

    payload = json.loads(user_settings.read_text(encoding="utf-8"))
    assert payload["general"]["preferredEditor"] == "vim"
    assert payload["mcpServers"] == {"other": {"command": "other-mcp", "args": []}}
    assert payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo start"
    assert payload["hooks"]["PreCompress"] == [
        {"hooks": [{"type": "command", "command": "echo keep", "name": "keep"}]}
    ]


def test_gemini_workspace_scope_is_rejected_in_phase_one(tmp_path, monkeypatch):
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)
    adapter = GeminiAdapter(home_dir=tmp_path / "home", project_root=tmp_path / "project")

    actions = adapter.plan(scope="workspace", remove=False)

    assert [action.status for action in actions] == ["cannot_apply", "cannot_apply"]
    assert all("project/user" in action.summary for action in actions)


def test_gemini_invalid_settings_reports_cannot_apply(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_settings = project_root / ".gemini" / "settings.json"
    project_settings.parent.mkdir(parents=True, exist_ok=True)
    project_settings.write_text('{"mcpServers":', encoding="utf-8")
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)

    adapter = GeminiAdapter(home_dir=home_dir, project_root=project_root)
    actions = adapter.plan(scope="project", remove=False)

    assert [action.status for action in actions] == ["cannot_apply", "cannot_apply"]
    assert all(action.path == project_settings for action in actions)


def test_gemini_prefers_host_cli_for_mcp_but_file_patch_for_hooks(tmp_path, monkeypatch):
    commands = []
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_settings = project_root / ".gemini" / "settings.json"

    def fake_run(argv, **kwargs):
        commands.append(argv)
        if argv[:4] == ["gemini", "mcp", "add", "--scope"]:
            _write_settings(project_settings, {"mcpServers": {"mempalace": _gemini_server_entry()}})
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: "/usr/bin/gemini")
    monkeypatch.setattr("mempalace.integrations.gemini.subprocess.run", fake_run)

    adapter = GeminiAdapter(home_dir=home_dir, project_root=project_root)
    mcp_action, hook_action = adapter.plan(scope="project", remove=False)
    mcp_result = adapter.apply(mcp_action)
    hook_result = adapter.apply(hook_action)

    assert mcp_action.use_host_cli is True
    assert hook_action.use_host_cli is False
    assert commands[0] == ["gemini", "mcp", "add", "--scope", "project", "mempalace", "mempalace-mcp"]
    payload = json.loads(project_settings.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["mempalace"] == _gemini_server_entry()
    assert payload["hooks"]["PreCompress"][0]["hooks"][0]["command"] == _gemini_hook_command()
    assert mcp_result.status == "skip"
    assert hook_result.status == "skip"


def test_gemini_remove_hook_uses_structured_operation_not_summary(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    user_settings = home_dir / ".gemini" / "settings.json"
    _write_settings(
        user_settings,
        {
            "hooks": {
                "PreCompress": [
                    _gemini_hook_definition(),
                ]
            },
        },
    )
    monkeypatch.setattr("mempalace.integrations.gemini.shutil.which", lambda _name: None)

    adapter = GeminiAdapter(home_dir=home_dir, project_root=project_root)
    actions = adapter.plan(scope="user", remove=True)
    hook_action = next(action for action in actions if action.kind == "hook")

    assert hook_action.operation == "remove"

    result = adapter.apply(replace(hook_action, summary="changed copy should not matter"))
    payload = json.loads(user_settings.read_text(encoding="utf-8"))

    assert result.summary == "Removed MemPalace PreCompress hook"
    assert payload.get("hooks", {}) == {}
