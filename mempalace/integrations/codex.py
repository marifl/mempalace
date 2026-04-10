"""Codex MCP adapter with host-CLI preference and safe TOML fallback."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

from .base import IntegrationAction
from .io import atomic_write_text

try:  # pragma: no cover - Python 3.11+ in tests, but keep a safe fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


_MEMPALACE_BLOCK_RE = re.compile(r"(?ms)^\[mcp_servers\.mempalace\]\n.*?(?=^\[|\Z)")


class CodexAdapter:
    name = "codex"

    def __init__(self, *, home_dir: Path | None = None, project_root: Path | None = None):
        self.home_dir = Path(home_dir).expanduser() if home_dir else Path.home()
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    @property
    def user_config_path(self) -> Path:
        return self.home_dir / ".codex" / "config.toml"

    @property
    def repo_plugin_path(self) -> Path:
        return self.project_root / ".codex-plugin" / "plugin.json"

    def discover(self) -> dict[str, object]:
        repo_plugin_has_mempalace = False
        if self.repo_plugin_path.exists():
            try:
                payload = json.loads(self.repo_plugin_path.read_text(encoding="utf-8"))
                repo_plugin_has_mempalace = "mempalace" in payload.get("mcpServers", {})
            except json.JSONDecodeError:
                repo_plugin_has_mempalace = False

        return {
            "cli_available": bool(shutil.which("codex")),
            "user_config_path": self.user_config_path,
            "user_config_exists": self.user_config_path.exists(),
            "repo_plugin_path": self.repo_plugin_path,
            "repo_plugin_has_mempalace": repo_plugin_has_mempalace,
        }

    def detect(self) -> bool:
        layers = self.discover()
        return bool(
            layers["cli_available"]
            or layers["user_config_exists"]
            or layers["repo_plugin_has_mempalace"]
        )

    def plan(self, *, palace=None, scope="auto", remove=False) -> IntegrationAction:
        if scope not in {"auto", "user"}:
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary="Codex integration supports only auto/user scope in Phase 1",
                path=self.user_config_path,
                requested_scope=scope,
                effective_scope="user",
            )

        layers = self.discover()
        parsed = self._load_user_config()
        shadowed_by = "repo-plugin" if layers["repo_plugin_has_mempalace"] else None

        if parsed["invalid"] and not layers["cli_available"]:
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary="Codex user config is invalid TOML; refusing fallback write",
                path=self.user_config_path,
                requested_scope=scope,
                effective_scope="user",
                shadowed_by=shadowed_by,
            )

        existing = parsed["mempalace"]
        desired_args = self._desired_args(palace)
        desired_present = self._matches_desired(existing, desired_args)
        use_host_cli = bool(layers["cli_available"])
        path = None if use_host_cli else self.user_config_path

        if layers["repo_plugin_has_mempalace"]:
            summary = (
                "Repo-local .codex-plugin already defines mempalace; user config would be shadowed"
            )
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=summary,
                path=self.user_config_path,
                requested_scope=scope,
                effective_scope="repo-plugin",
                shadowed_by="repo-plugin",
                use_host_cli=use_host_cli,
                command_args=tuple(desired_args),
            )

        if remove:
            status = "update" if existing else "skip"
            summary = (
                "Remove MemPalace MCP registration"
                if existing
                else "MemPalace MCP registration not present"
            )
            return IntegrationAction(
                host=self.name,
                kind="remove",
                status=status,
                summary=summary,
                path=path,
                requested_scope=scope,
                effective_scope="user",
                shadowed_by=shadowed_by,
                use_host_cli=use_host_cli,
                command_args=tuple(desired_args),
            )

        if desired_present:
            return IntegrationAction(
                host=self.name,
                kind="mcp",
                status="skip",
                summary="MemPalace MCP registration already present",
                path=path,
                requested_scope=scope,
                effective_scope="user",
                shadowed_by=shadowed_by,
                use_host_cli=use_host_cli,
                command_args=tuple(desired_args),
            )

        status = "update" if existing else "create"
        summary = "Update MemPalace MCP registration" if existing else "Add MemPalace MCP server"
        return IntegrationAction(
            host=self.name,
            kind="mcp",
            status=status,
            summary=summary,
            path=path,
            requested_scope=scope,
            effective_scope="user",
            shadowed_by=shadowed_by,
            use_host_cli=use_host_cli,
            command_args=tuple(desired_args),
        )

    def apply(self, action: IntegrationAction) -> IntegrationAction:
        if action.status in {"skip", "cannot_apply"}:
            return action
        if action.use_host_cli:
            return self._apply_with_cli(action)
        return self._apply_with_file_patch(action)

    def _apply_with_cli(self, action: IntegrationAction) -> IntegrationAction:
        if action.kind == "remove":
            command = ["codex", "mcp", "remove", "mempalace"]
        else:
            command = ["codex", "mcp", "add", "mempalace", "--", "mempalace-mcp"]
            command.extend(action.command_args)

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Codex command failed"
            raise RuntimeError(detail)

        verify = subprocess.run(
            ["codex", "mcp", "get", "mempalace"],
            capture_output=True,
            text=True,
            check=False,
        )
        if action.kind == "remove":
            if verify.returncode == 0:
                raise RuntimeError("Codex still reports mempalace after remove")
            return replace(action, status="skip", summary="Removed MemPalace MCP registration")
        if verify.returncode != 0:
            raise RuntimeError("Codex did not report mempalace after add")
        return replace(action, status="skip", summary="MemPalace MCP registration present")

    def _apply_with_file_patch(self, action: IntegrationAction) -> IntegrationAction:
        current = ""
        if action.path.exists():
            current = action.path.read_text(encoding="utf-8")

        if action.kind == "remove":
            updated = self._remove_mempalace_block(current)
        else:
            updated = self._upsert_mempalace_block(current, list(action.command_args))

        backup_path = atomic_write_text(
            action.path,
            updated,
            host=self.name,
            validator=self._validate_toml_file,
        )
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _load_user_config(self) -> dict[str, object]:
        if not self.user_config_path.exists():
            return {"invalid": False, "mempalace": None}
        text = self.user_config_path.read_text(encoding="utf-8")
        if tomllib is None:
            return {"invalid": True, "mempalace": None}
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {"invalid": True, "mempalace": None}
        server = data.get("mcp_servers", {}).get("mempalace")
        return {"invalid": False, "mempalace": server if isinstance(server, dict) else None}

    @staticmethod
    def _desired_args(palace) -> list[str]:
        if not palace:
            return []
        return ["--palace", str(Path(palace).expanduser())]

    @classmethod
    def _matches_desired(cls, existing, desired_args: list[str]) -> bool:
        if not isinstance(existing, dict):
            return False
        if existing.get("command") != "mempalace-mcp":
            return False
        return list(existing.get("args", [])) == desired_args

    @classmethod
    def _upsert_mempalace_block(cls, text: str, desired_args: list[str]) -> str:
        desired = (
            '[mcp_servers.mempalace]\n'
            'command = "mempalace-mcp"\n'
            f"args = {json.dumps(desired_args)}\n"
        )
        if _MEMPALACE_BLOCK_RE.search(text):
            updated = _MEMPALACE_BLOCK_RE.sub(desired, text, count=1)
        else:
            updated = text
            if updated and not updated.endswith("\n"):
                updated += "\n"
            if updated.strip():
                updated += "\n"
            updated += desired
        return updated

    @classmethod
    def _remove_mempalace_block(cls, text: str) -> str:
        updated = _MEMPALACE_BLOCK_RE.sub("", text, count=1)
        updated = re.sub(r"\n{3,}", "\n\n", updated)
        return updated.lstrip("\n")

    @staticmethod
    def _validate_toml_file(path: Path) -> None:
        if tomllib is None:
            raise RuntimeError("TOML validation unavailable on this Python version")
        tomllib.loads(path.read_text(encoding="utf-8"))
