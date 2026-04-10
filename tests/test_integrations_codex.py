import json
from pathlib import Path
from subprocess import CompletedProcess

from mempalace.integrations.codex import CodexAdapter


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
    action = adapter.plan(palace=None, scope="auto", remove=False)
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
    action = adapter.plan(palace=palace, scope="auto", remove=False)
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
    action = adapter.plan(palace=None, scope="auto", remove=False)

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
    action = adapter.plan(palace=None, scope="user", remove=False)

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
    action = adapter.plan(palace=None, scope="user", remove=True)

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
    action = adapter.plan(palace=None, scope="auto", remove=True)
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
    action = adapter.plan(palace=None, scope="auto", remove=False)

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
    action = adapter.plan(palace=None, scope="auto", remove=False)

    assert action.status == "skip"
    assert "already present" in action.summary


def test_codex_fallback_writes_custom_palace_args(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "repo"
    user_config = home_dir / ".codex" / "config.toml"
    palace = tmp_path / "palace"
    monkeypatch.setattr("mempalace.integrations.codex.shutil.which", lambda _name: None)

    adapter = CodexAdapter(home_dir=home_dir, project_root=project_root)
    action = adapter.plan(palace=palace, scope="auto", remove=False)
    adapter.apply(action)

    content = user_config.read_text(encoding="utf-8")
    assert 'command = "mempalace-mcp"' in content
    assert f'args = ["--palace", "{palace}"]' in content
