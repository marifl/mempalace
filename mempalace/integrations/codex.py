"""Codex MCP adapter with host-CLI preference and safe fallback patching."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Optional

from .base import IntegrationAction
from .io import atomic_write_text

try:  # pragma: no cover - Python 3.11+ in tests, but keep a safe fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


_MEMPALACE_BLOCK_RE = re.compile(r"(?ms)^\[mcp_servers\.mempalace\][^\n]*\n.*?(?=^\[|\Z)")


class CodexAdapter:
    name = "codex"
    _HOOK_EVENTS = (
        ("SessionStart", "session-start"),
        ("Stop", "stop"),
    )

    def __init__(self, *, home_dir: Path | None = None, project_root: Path | None = None):
        self.home_dir = Path(home_dir).expanduser() if home_dir else Path.home()
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    @property
    def user_config_path(self) -> Path:
        return self.home_dir / ".codex" / "config.toml"

    @property
    def user_hooks_path(self) -> Path:
        return self.home_dir / ".codex" / "hooks.json"

    @property
    def repo_plugin_path(self) -> Path:
        return self.project_root / ".codex-plugin" / "plugin.json"

    @property
    def repo_plugin_hooks_path(self) -> Path:
        return self.project_root / ".codex-plugin" / "hooks.json"

    def discover(self) -> dict[str, object]:
        repo_plugin_has_mempalace = False
        if self.repo_plugin_path.exists():
            try:
                payload = json.loads(self.repo_plugin_path.read_text(encoding="utf-8"))
                repo_plugin_has_mempalace = "mempalace" in payload.get("mcpServers", {})
            except json.JSONDecodeError:
                repo_plugin_has_mempalace = False

        user_hooks_payload = self._load_json_object(self.user_hooks_path)
        repo_plugin_hooks_payload = self._load_json_object(self.repo_plugin_hooks_path)

        return {
            "cli_available": bool(shutil.which("codex")),
            "user_config_path": self.user_config_path,
            "user_config_exists": self.user_config_path.exists(),
            "user_hooks_path": self.user_hooks_path,
            "user_hooks_exists": self.user_hooks_path.exists(),
            "user_hooks_invalid": user_hooks_payload["invalid"],
            "user_has_mempalace_hooks": self._has_any_mempalace_hooks(user_hooks_payload["data"]),
            "repo_plugin_path": self.repo_plugin_path,
            "repo_plugin_has_mempalace": repo_plugin_has_mempalace,
            "repo_plugin_hooks_path": self.repo_plugin_hooks_path,
            "repo_plugin_has_mempalace_hooks": self._has_any_mempalace_hooks(
                repo_plugin_hooks_payload["data"]
            ),
        }

    def detect(self) -> bool:
        layers = self.discover()
        return bool(
            layers["cli_available"]
            or layers["user_config_exists"]
            or layers["user_hooks_exists"]
            or layers["repo_plugin_has_mempalace"]
            or layers["repo_plugin_has_mempalace_hooks"]
        )

    def plan(self, *, palace=None, scope="auto", remove=False) -> list[IntegrationAction]:
        if scope not in {"auto", "user"}:
            summary = "Codex integration supports only auto/user scope in Phase 1"
            return [
                IntegrationAction(
                    host=self.name,
                    kind="remove" if remove else "mcp",
                    status="cannot_apply",
                    summary=summary,
                    path=self.user_config_path,
                    requested_scope=scope,
                    effective_scope="user",
                ),
                IntegrationAction(
                    host=self.name,
                    kind="hook",
                    status="cannot_apply",
                    summary=summary,
                    path=self.user_hooks_path,
                    requested_scope=scope,
                    effective_scope="user",
                ),
            ]

        layers = self.discover()
        return [
            self._plan_mcp_action(layers=layers, palace=palace, scope=scope, remove=remove),
            self._plan_hook_action(layers=layers, scope=scope, remove=remove),
        ]

    def apply(self, action: IntegrationAction) -> IntegrationAction:
        if action.status in {"skip", "cannot_apply"}:
            return action
        if action.kind == "hook":
            return self._apply_hook_with_file(action)
        if action.use_host_cli:
            return self._apply_with_cli(action)
        return self._apply_with_file_patch(action)

    def _plan_mcp_action(self, *, layers: dict[str, object], palace, scope: str, remove: bool) -> IntegrationAction:
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

    def _plan_hook_action(self, *, layers: dict[str, object], scope: str, remove: bool) -> IntegrationAction:
        payload = self._load_json_object(self.user_hooks_path)

        if layers["repo_plugin_has_mempalace_hooks"]:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary="Repo-local .codex-plugin already defines MemPalace hooks; user hooks would be shadowed",
                path=self.user_hooks_path,
                requested_scope=scope,
                effective_scope="repo-plugin",
                shadowed_by="repo-plugin",
                operation="remove" if remove else "upsert",
            )

        if payload["invalid"]:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary="Codex user hooks are invalid JSON; refusing fallback write",
                path=self.user_hooks_path,
                requested_scope=scope,
                effective_scope="user",
                operation="remove" if remove else "upsert",
            )

        if not self._supported_hooks_shape(payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary="Codex user hooks shape is unsupported for fallback write",
                path=self.user_hooks_path,
                requested_scope=scope,
                effective_scope="user",
                operation="remove" if remove else "upsert",
            )

        existing = self._has_any_mempalace_hooks(payload["data"])
        if remove:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="update" if existing else "skip",
                summary="Remove MemPalace hooks" if existing else "MemPalace hooks not present",
                path=self.user_hooks_path,
                requested_scope=scope,
                effective_scope="user",
                operation="remove",
            )

        if self._hooks_match(payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="skip",
                summary="MemPalace hooks already present",
                path=self.user_hooks_path,
                requested_scope=scope,
                effective_scope="user",
            )

        return IntegrationAction(
            host=self.name,
            kind="hook",
            status="update" if existing else "create",
            summary="Update MemPalace hooks" if existing else "Add MemPalace hooks",
            path=self.user_hooks_path,
            requested_scope=scope,
            effective_scope="user",
            operation="upsert",
        )

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
        self._verify_target_file_patch(action)
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _apply_hook_with_file(self, action: IntegrationAction) -> IntegrationAction:
        payload = self._load_json_object(action.path)
        current = payload["data"] if isinstance(payload["data"], dict) else {}
        if not self._supported_hooks_shape(current):
            raise RuntimeError("Codex user hooks shape is unsupported for fallback write")

        if action.operation == "remove":
            updated = self._remove_hooks(current)
        else:
            updated = self._upsert_hooks(current)

        backup_path = atomic_write_text(
            action.path,
            json.dumps(updated, indent=2) + "\n",
            host=self.name,
            validator=self._validate_json_file,
        )
        self._verify_target_hooks(action.path, action)
        summary = "Removed MemPalace hooks" if action.operation == "remove" else "MemPalace hooks present"
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
    def _load_json_object(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"invalid": False, "data": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"invalid": True, "data": None}
        if not isinstance(data, dict):
            return {"invalid": True, "data": None}
        return {"invalid": False, "data": data}

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

    @classmethod
    def _build_hook_handler(cls, hook_name: str) -> dict[str, object]:
        return {
            "type": "command",
            "command": f"mempalace hook run --hook {hook_name} --harness codex",
        }

    @classmethod
    def _build_hook_group(cls, hook_name: str) -> dict[str, object]:
        return {
            "matcher": "*",
            "hooks": [cls._build_hook_handler(hook_name)],
        }

    @staticmethod
    def _supported_hooks_shape(data: object) -> bool:
        if data is None:
            return True
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks")
        return hooks is None or isinstance(hooks, dict)

    @classmethod
    def _find_hook_group(cls, hooks: dict[str, object], event: str) -> Optional[dict[str, object]]:
        definitions = hooks.get(event)
        if not isinstance(definitions, list):
            return None
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            hook_entries = definition.get("hooks")
            if not isinstance(hook_entries, list):
                continue
            if any(cls._is_mempalace_handler(hook) for hook in hook_entries):
                return definition
        return None

    @classmethod
    def _has_any_mempalace_hooks(cls, data: object) -> bool:
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return False
        for event, _hook_name in cls._HOOK_EVENTS:
            if cls._find_hook_group(hooks, event) is not None:
                return True
        return False

    @classmethod
    def _hooks_match(cls, data: object) -> bool:
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return False
        for event, hook_name in cls._HOOK_EVENTS:
            group = cls._find_hook_group(hooks, event)
            if group is None:
                return False
            if group != cls._build_hook_group(hook_name):
                return False
        return True

    @classmethod
    def _is_mempalace_handler(cls, handler: object) -> bool:
        if not isinstance(handler, dict):
            return False
        command = handler.get("command")
        for _event, hook_name in cls._HOOK_EVENTS:
            if command == f"mempalace hook run --hook {hook_name} --harness codex":
                return True
        return False

    @classmethod
    def _strip_mempalace_handlers(cls, definitions: object) -> list[dict[str, object]]:
        if not isinstance(definitions, list):
            return []
        cleaned: list[dict[str, object]] = []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            hook_entries = definition.get("hooks")
            if not isinstance(hook_entries, list):
                cleaned.append(definition)
                continue
            kept = [hook for hook in hook_entries if not cls._is_mempalace_handler(hook)]
            if kept:
                migrated = dict(definition)
                migrated["hooks"] = kept
                cleaned.append(migrated)
        return cleaned

    @classmethod
    def _upsert_hooks(cls, data: dict[str, object]) -> dict[str, object]:
        updated = dict(data)
        hooks = updated.get("hooks")
        hooks_dict = dict(hooks) if isinstance(hooks, dict) else {}
        for event, hook_name in cls._HOOK_EVENTS:
            definitions = cls._strip_mempalace_handlers(hooks_dict.get(event))
            definitions.append(cls._build_hook_group(hook_name))
            hooks_dict[event] = definitions
        updated["hooks"] = hooks_dict
        return updated

    @classmethod
    def _remove_hooks(cls, data: dict[str, object]) -> dict[str, object]:
        updated = dict(data)
        hooks = updated.get("hooks")
        if not isinstance(hooks, dict):
            return updated
        hooks_dict = dict(hooks)
        for event, _hook_name in cls._HOOK_EVENTS:
            definitions = cls._strip_mempalace_handlers(hooks_dict.get(event))
            if definitions:
                hooks_dict[event] = definitions
            else:
                hooks_dict.pop(event, None)
        if hooks_dict:
            updated["hooks"] = hooks_dict
        else:
            updated.pop("hooks", None)
        return updated

    @staticmethod
    def _validate_toml_file(path: Path) -> None:
        if tomllib is None:
            raise RuntimeError("TOML validation unavailable on this Python version")
        tomllib.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _validate_json_file(path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Codex hooks file must be a JSON object")

    def _verify_target_file_patch(self, action: IntegrationAction) -> None:
        parsed_after = self._load_user_config()
        if parsed_after["invalid"]:
            raise RuntimeError("Codex config became invalid after write")
        current = parsed_after["mempalace"]
        if action.kind == "remove":
            if current is not None:
                raise RuntimeError("Codex config still contains mempalace after remove")
            return
        if not self._matches_desired(current, list(action.command_args)):
            raise RuntimeError("Codex config did not verify mempalace after write")

    def _verify_target_hooks(self, path: Path, action: IntegrationAction) -> None:
        payload = self._load_json_object(path)
        data = payload["data"]
        if payload["invalid"] or not self._supported_hooks_shape(data):
            raise RuntimeError("Codex hooks became invalid after write")
        if action.operation == "remove":
            if self._has_any_mempalace_hooks(data):
                raise RuntimeError("Codex hooks still contain mempalace after remove")
            return
        if not self._hooks_match(data):
            raise RuntimeError("Codex hooks did not verify mempalace after write")
