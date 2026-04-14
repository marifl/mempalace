import json
import subprocess
import sys
import re
from pathlib import Path

from mempalace import __version__
from mempalace.integrations.manager import get_adapters
from mempalace.mcp_server import handle_request


def _expected_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match is not None, "Could not find project version in pyproject.toml"
    return match.group(1)


def test_package_version_matches_pyproject():
    assert __version__ == _expected_version()


def test_pyproject_defines_cli_scripts():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert 'mempalace = "mempalace:main"' in content
    assert 'mempalace-mcp = "mempalace.mcp_main:main"' in content


def test_pyproject_constrains_python_before_3_14():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match is not None, "Could not find requires-python in pyproject.toml"
    assert match.group(1) == ">=3.9,<3.14"


def test_mcp_main_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "mempalace.mcp_main", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--palace" in result.stdout


def test_mcp_initialize_reports_package_version():
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["serverInfo"]["version"] == _expected_version()


def test_manager_registers_supported_adapters():
    names = [adapter.name for adapter in get_adapters()]
    assert names == ["claude", "codex", "gemini"]


def test_legacy_plugin_manifests_match_package_version():
    repo_root = Path(__file__).resolve().parents[1]
    expected = _expected_version()

    claude_plugin = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_plugin = json.loads((repo_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )

    assert claude_plugin["version"] == expected
    assert codex_plugin["version"] == expected
    assert marketplace["plugins"][0]["version"] == expected


def test_legacy_plugin_manifests_use_mempalace_mcp():
    repo_root = Path(__file__).resolve().parents[1]
    claude_plugin = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_plugin = json.loads((repo_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert claude_plugin["mcpServers"]["mempalace"]["command"] == "mempalace-mcp"
    assert codex_plugin["mcpServers"]["mempalace"]["command"] == "mempalace-mcp"


def test_legacy_hook_wrappers_use_mempalace_cli():
    repo_root = Path(__file__).resolve().parents[1]
    wrappers = [
        repo_root / ".claude-plugin" / "hooks" / "mempal-stop-hook.sh",
        repo_root / ".claude-plugin" / "hooks" / "mempal-precompact-hook.sh",
        repo_root / ".codex-plugin" / "hooks" / "mempal-hook.sh",
    ]

    for wrapper in wrappers:
        content = wrapper.read_text(encoding="utf-8")
        assert "mempalace hook run" in content
        assert "python3 -m mempalace" not in content


def test_legacy_codex_hook_manifest_uses_supported_events():
    repo_root = Path(__file__).resolve().parents[1]
    hooks_manifest = json.loads((repo_root / ".codex-plugin" / "hooks.json").read_text(encoding="utf-8"))

    assert set(hooks_manifest["hooks"]) == {"SessionStart", "Stop"}


def test_readme_codex_hook_example_matches_supported_codex_events():
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    match = re.search(r"For Codex,.*?```json\n(.*?)\n```", readme, re.DOTALL)

    assert match is not None
    block = match.group(1)
    assert '"SessionStart"' in block
    assert '"Stop"' in block
    assert '"PreCompact"' not in block
    assert "--harness codex" in block
    assert "--harness claude-code" not in block
