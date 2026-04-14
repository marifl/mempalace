"""Claude MCP adapter with effective-scope detection and validated writes."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from .base import IntegrationAction
from .io import atomic_write_text


class ClaudeAdapter:
    name = "claude"
    _HOOK_EVENTS = (
        ("SessionStart", "session-start"),
        ("Stop", "stop"),
        ("PreCompact", "precompact"),
    )

    def __init__(self, *, home_dir: Path | None = None, project_root: Path | None = None):
        self.home_dir = Path(home_dir).expanduser() if home_dir else Path.home()
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    @property
    def user_config_path(self) -> Path:
        return self.home_dir / ".claude.json"

    @property
    def project_config_path(self) -> Path:
        return self.project_root / ".mcp.json"

    @property
    def local_config_path(self) -> Path:
        return self.home_dir / ".claude.json"

    @property
    def user_settings_path(self) -> Path:
        return self.home_dir / ".claude" / "settings.json"

    @property
    def project_settings_path(self) -> Path:
        return self.project_root / ".claude" / "settings.json"

    @property
    def local_settings_path(self) -> Path:
        return self.project_root / ".claude" / "settings.local.json"

    def discover(self) -> dict[str, object]:
        user_payload = self._load_json(self.user_config_path)
        project_payload = self._load_json(self.project_config_path)
        user_settings_payload = self._load_json(self.user_settings_path)
        project_settings_payload = self._load_json(self.project_settings_path)
        local_settings_payload = self._load_json(self.local_settings_path)

        user_data = user_payload["data"] if not user_payload["invalid"] else None
        project_data = project_payload["data"] if not project_payload["invalid"] else None
        user_settings_data = user_settings_payload["data"] if not user_settings_payload["invalid"] else None
        project_settings_data = (
            project_settings_payload["data"] if not project_settings_payload["invalid"] else None
        )
        local_settings_data = (
            local_settings_payload["data"] if not local_settings_payload["invalid"] else None
        )

        local_data = None
        if isinstance(user_data, dict):
            local_data = self._local_project_entry(user_data)

        return {
            "cli_available": bool(shutil.which("claude")),
            "user_config_path": self.user_config_path,
            "project_config_path": self.project_config_path,
            "local_config_path": self.local_config_path,
            "user_config_exists": self.user_config_path.exists(),
            "project_config_exists": self.project_config_path.exists(),
            "local_config_exists": isinstance(local_data, dict),
            "user_has_mempalace": isinstance(self._mcp_servers(user_data), dict)
            and "mempalace" in self._mcp_servers(user_data),
            "project_has_mempalace": isinstance(self._mcp_servers(project_data), dict)
            and "mempalace" in self._mcp_servers(project_data),
            "local_has_mempalace": isinstance(local_data, dict)
            and "mempalace" in self._mcp_servers(local_data),
            "user_invalid": user_payload["invalid"],
            "project_invalid": project_payload["invalid"],
            "user_settings_path": self.user_settings_path,
            "project_settings_path": self.project_settings_path,
            "local_settings_path": self.local_settings_path,
            "user_settings_exists": self.user_settings_path.exists(),
            "project_settings_exists": self.project_settings_path.exists(),
            "local_settings_exists": self.local_settings_path.exists(),
            "user_settings_invalid": user_settings_payload["invalid"],
            "project_settings_invalid": project_settings_payload["invalid"],
            "local_settings_invalid": local_settings_payload["invalid"],
            "user_has_mempalace_hooks": self._has_any_mempalace_hooks(user_settings_data),
            "project_has_mempalace_hooks": self._has_any_mempalace_hooks(project_settings_data),
            "local_has_mempalace_hooks": self._has_any_mempalace_hooks(local_settings_data),
        }

    def detect(self) -> bool:
        layers = self.discover()
        return bool(
            layers["cli_available"]
            or layers["user_config_exists"]
            or layers["project_config_exists"]
            or layers["local_config_exists"]
            or layers["user_settings_exists"]
            or layers["project_settings_exists"]
            or layers["local_settings_exists"]
        )

    def plan(self, *, palace=None, scope="auto", remove=False) -> list[IntegrationAction]:
        if scope not in {"auto", "user", "local", "project"}:
            return [
                IntegrationAction(
                    host=self.name,
                    kind="remove" if remove else "mcp",
                    status="cannot_apply",
                    summary="Claude integration supports only auto, local, user, and project scope",
                    requested_scope=scope,
                ),
                IntegrationAction(
                    host=self.name,
                    kind="hook",
                    status="cannot_apply",
                    summary="Claude integration supports only auto, local, user, and project scope",
                    requested_scope=scope,
                ),
            ]

        layers = self.discover()
        target_scope = self._resolve_target_scope(scope, layers)
        hook_target_scope = self._resolve_hook_target_scope(scope, layers)
        if target_scope is None or hook_target_scope is None:
            return [
                IntegrationAction(
                    host=self.name,
                    kind="remove" if remove else "mcp",
                    status="cannot_apply",
                    summary="Claude integration has no supported writable target",
                    requested_scope=scope,
                ),
                IntegrationAction(
                    host=self.name,
                    kind="hook",
                    status="cannot_apply",
                    summary="Claude integration has no supported writable hook target",
                    requested_scope=scope,
                ),
            ]
        target_path = self._target_path(target_scope)
        hook_target_path = self._hook_target_path(hook_target_scope)
        target_payload = self._load_json(target_path)
        hook_target_payload = self._load_json(hook_target_path)
        use_host_cli = bool(layers["cli_available"])
        command_args = tuple(self._desired_args(palace))

        return [
            self._plan_mcp_action(
                requested_scope=scope,
                target_scope=target_scope,
                target_path=target_path,
                target_payload=target_payload,
                use_host_cli=use_host_cli,
                command_args=command_args,
                layers=layers,
                remove=remove,
            ),
            self._plan_hook_action(
                requested_scope=scope,
                target_scope=hook_target_scope,
                target_path=hook_target_path,
                target_payload=hook_target_payload,
                layers=layers,
                remove=remove,
            ),
        ]

    def apply(self, action: IntegrationAction) -> IntegrationAction:
        if action.status in {"skip", "cannot_apply"}:
            return action
        if action.kind == "hook":
            return self._apply_hook_with_file(action)
        if action.use_host_cli:
            return self._apply_with_cli(action)
        return self._apply_with_file(action)

    def _plan_mcp_action(
        self,
        *,
        requested_scope: str,
        target_scope: str,
        target_path: Path,
        target_payload: dict[str, object],
        use_host_cli: bool,
        command_args: tuple[str, ...],
        layers: dict[str, object],
        remove: bool,
    ) -> IntegrationAction:
        if target_payload["invalid"] and not use_host_cli:
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=f"Claude {target_scope} config is invalid JSON; refusing fallback write",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=False,
                command_args=command_args,
            )

        if not use_host_cli and not self._supported_json_shape(target_payload["data"], target_scope):
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=f"Claude {target_scope} config shape is unsupported for fallback write",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=False,
                command_args=command_args,
            )

        if self._is_shadowed(target_scope, layers, requested_scope):
            shadowed_by = self._shadowing_scope(target_scope, layers)
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=f"Claude {target_scope} config would be shadowed by {shadowed_by}",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=shadowed_by,
                shadowed_by=shadowed_by,
                use_host_cli=use_host_cli,
                command_args=command_args,
            )

        target_server = self._target_server(target_payload["data"], target_scope)
        desired_server = self._build_server_entry(command_args)

        if remove:
            if not target_server:
                return IntegrationAction(
                    host=self.name,
                    kind="remove",
                    status="skip",
                    summary="MemPalace MCP registration not present",
                    path=target_path,
                    requested_scope=requested_scope,
                    effective_scope=target_scope,
                    use_host_cli=use_host_cli,
                    command_args=command_args,
                )
            return IntegrationAction(
                host=self.name,
                kind="remove",
                status="update",
                summary="Remove MemPalace MCP registration",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=use_host_cli,
                command_args=command_args,
            )

        if self._servers_match(target_server, desired_server):
            return IntegrationAction(
                host=self.name,
                kind="mcp",
                status="skip",
                summary="MemPalace MCP registration already present",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=use_host_cli,
                command_args=command_args,
            )

        status = "update" if target_server else "create"
        summary = "Update MemPalace MCP registration" if target_server else "Add MemPalace MCP server"
        return IntegrationAction(
            host=self.name,
            kind="mcp",
            status=status,
            summary=summary,
            path=target_path,
            requested_scope=requested_scope,
            effective_scope=target_scope,
            use_host_cli=use_host_cli,
            command_args=command_args,
        )

    def _plan_hook_action(
        self,
        *,
        requested_scope: str,
        target_scope: str,
        target_path: Path,
        target_payload: dict[str, object],
        layers: dict[str, object],
        remove: bool,
    ) -> IntegrationAction:
        if target_payload["invalid"]:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary=f"Claude {target_scope} settings are invalid JSON; cannot patch hooks",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        if not self._supported_hook_settings_shape(target_payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary=f"Claude {target_scope} settings shape is unsupported for hook patching",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        if self._is_hook_scope_shadowed(target_scope, layers, requested_scope):
            shadowed_by = self._hook_shadowing_scope(target_scope, layers)
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary=f"Claude {target_scope} settings would be shadowed by {shadowed_by}",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=shadowed_by,
                shadowed_by=shadowed_by,
            )

        existing = self._has_any_mempalace_hooks(target_payload["data"])
        if remove:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="update" if existing else "skip",
                summary="Remove MemPalace hooks" if existing else "MemPalace hooks not present",
                operation="remove",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        if self._hooks_match(target_payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="skip",
                summary="MemPalace hooks already present",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        return IntegrationAction(
            host=self.name,
            kind="hook",
            status="update" if existing else "create",
            summary="Update MemPalace hooks" if existing else "Add MemPalace hooks",
            operation="upsert",
            path=target_path,
            requested_scope=requested_scope,
            effective_scope=target_scope,
        )

    def _apply_with_cli(self, action: IntegrationAction) -> IntegrationAction:
        if action.kind == "remove":
            command = ["claude", "mcp", "remove", "mempalace", "--scope", action.effective_scope]
        else:
            command = [
                "claude",
                "mcp",
                "add",
                "mempalace",
                "--scope",
                action.effective_scope,
                "--",
                "mempalace-mcp",
            ]
            command.extend(action.command_args)

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Claude command failed"
            raise RuntimeError(detail)

        self._verify_target_registration(self._target_path(action.effective_scope), action)
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary)

    def _apply_with_file(self, action: IntegrationAction) -> IntegrationAction:
        path = self._target_path(action.effective_scope)
        payload = self._load_json(path)
        data = payload["data"] if isinstance(payload["data"], dict) else {}

        if not self._supported_json_shape(data, action.effective_scope):
            raise RuntimeError(
                f"Claude {action.effective_scope} config shape is unsupported for fallback write"
            )

        if action.kind == "remove":
            updated = self._remove_target(data, action.effective_scope)
        else:
            updated = self._upsert_target(
                data,
                action.effective_scope,
                self._build_server_entry(list(action.command_args)),
            )

        backup_path = atomic_write_text(
            path,
            json.dumps(updated, indent=2) + "\n",
            host=self.name,
            validator=self._validate_json_file,
        )
        self._verify_target_registration(path, action)
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _apply_hook_with_file(self, action: IntegrationAction) -> IntegrationAction:
        path = self._hook_target_path(action.effective_scope)
        payload = self._load_json(path)
        data = payload["data"] if isinstance(payload["data"], dict) else {}

        if action.operation == "remove":
            updated = self._remove_hooks(data)
        else:
            updated = self._upsert_hooks(data)

        backup_path = atomic_write_text(
            path,
            json.dumps(updated, indent=2) + "\n",
            host=self.name,
            validator=self._validate_json_file,
        )
        self._verify_target_hooks(path, action)
        summary = "Removed MemPalace hooks" if action.operation == "remove" else "MemPalace hooks present"
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _resolve_target_scope(self, scope: str, layers: dict[str, object]) -> str:
        if scope != "auto":
            return scope
        for candidate, exists_key in (
            ("local", "local_config_exists"),
            ("project", "project_config_exists"),
            ("user", "user_config_exists"),
        ):
            if not layers[exists_key]:
                continue
            path = self._target_path(candidate)
            payload = self._load_json(path)
            if payload["invalid"]:
                continue
            if self._supported_json_shape(payload["data"], candidate):
                return candidate
        return "user"

    def _resolve_hook_target_scope(self, scope: str, layers: dict[str, object]) -> str:
        if scope != "auto":
            return scope
        for candidate, exists_key in (
            ("local", "local_settings_exists"),
            ("project", "project_settings_exists"),
            ("user", "user_settings_exists"),
        ):
            if not layers[exists_key]:
                continue
            payload = self._load_json(self._hook_target_path(candidate))
            if payload["invalid"]:
                continue
            if self._supported_hook_settings_shape(payload["data"]):
                return candidate
        return "user"

    def _is_shadowed(self, target_scope: str, layers: dict[str, object], requested_scope: str) -> bool:
        if requested_scope == "auto":
            return False
        return self._shadowing_scope(target_scope, layers) is not None

    @staticmethod
    def _shadowing_scope(target_scope: str, layers: dict[str, object]) -> Optional[str]:
        if target_scope == "user":
            if layers["local_config_exists"]:
                return "local"
            if layers["project_config_exists"]:
                return "project"
        elif target_scope == "project":
            if layers["local_config_exists"]:
                return "local"
        return None

    def _is_hook_scope_shadowed(
        self,
        target_scope: str,
        layers: dict[str, object],
        requested_scope: str,
    ) -> bool:
        if requested_scope == "auto":
            return False
        return self._hook_shadowing_scope(target_scope, layers) is not None

    @staticmethod
    def _hook_shadowing_scope(target_scope: str, layers: dict[str, object]) -> Optional[str]:
        if target_scope == "user":
            if layers["local_settings_exists"]:
                return "local"
            if layers["project_settings_exists"]:
                return "project"
        elif target_scope == "project":
            if layers["local_settings_exists"]:
                return "local"
        return None

    def _target_path(self, scope: str) -> Path:
        if scope == "project":
            return self.project_config_path
        return self.user_config_path

    def _hook_target_path(self, scope: str) -> Path:
        if scope == "project":
            return self.project_settings_path
        if scope == "local":
            return self.local_settings_path
        if scope == "user":
            return self.user_settings_path
        raise RuntimeError(f"Unsupported Claude hook scope: {scope}")

    def _target_server(self, data: object, scope: str) -> Optional[dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        if scope == "project":
            return self._mcp_servers(data).get("mempalace") if isinstance(self._mcp_servers(data), dict) else None
        if scope == "local":
            project = self._local_project_entry(data)
            if not isinstance(project, dict):
                return None
            return self._mcp_servers(project).get("mempalace") if isinstance(self._mcp_servers(project), dict) else None
        return self._mcp_servers(data).get("mempalace") if isinstance(self._mcp_servers(data), dict) else None

    def _upsert_target(self, data: dict[str, object], scope: str, server: dict[str, object]) -> dict[str, object]:
        updated = dict(data)
        if scope == "project":
            updated["mcpServers"] = self._with_server(self._mcp_servers(updated), server)
            return updated
        if scope == "local":
            projects = dict(updated.get("projects", {}))
            project_key = str(self.project_root.resolve())
            project_entry = dict(projects.get(project_key, {}))
            project_entry["mcpServers"] = self._with_server(
                self._mcp_servers(project_entry),
                server,
            )
            projects[project_key] = project_entry
            updated["projects"] = projects
            return updated
        updated["mcpServers"] = self._with_server(self._mcp_servers(updated), server)
        return updated

    def _remove_target(self, data: dict[str, object], scope: str) -> dict[str, object]:
        updated = dict(data)
        if scope == "project":
            servers = dict(self._mcp_servers(updated))
            servers.pop("mempalace", None)
            updated["mcpServers"] = servers
            return updated
        if scope == "local":
            projects = dict(updated.get("projects", {}))
            project_key = str(self.project_root.resolve())
            project_entry = dict(projects.get(project_key, {}))
            servers = dict(self._mcp_servers(project_entry))
            servers.pop("mempalace", None)
            project_entry["mcpServers"] = servers
            projects[project_key] = project_entry
            updated["projects"] = projects
            return updated
        servers = dict(self._mcp_servers(updated))
        servers.pop("mempalace", None)
        updated["mcpServers"] = servers
        return updated

    def _load_json(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {"invalid": False, "data": None}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"invalid": True, "data": None}
        if not isinstance(data, dict):
            return {"invalid": True, "data": None}
        return {"invalid": False, "data": data}

    def _local_project_entry(self, data: dict[str, object]) -> Optional[dict[str, object]]:
        projects = data.get("projects")
        if not isinstance(projects, dict):
            return None
        entry = projects.get(str(self.project_root.resolve()))
        return entry if isinstance(entry, dict) else None

    @staticmethod
    def _mcp_servers(data: object) -> dict[str, object]:
        if not isinstance(data, dict):
            return {}
        servers = data.get("mcpServers")
        return servers if isinstance(servers, dict) else {}

    def _supported_json_shape(self, data: object, scope: str) -> bool:
        if data is None:
            return True
        if not isinstance(data, dict):
            return False
        if scope == "project":
            servers = data.get("mcpServers")
            return servers is None or isinstance(servers, dict)
        if scope == "local":
            projects = data.get("projects")
            if projects is not None and not isinstance(projects, dict):
                return False
            project = self._local_project_entry(data)
            if project is not None:
                servers = project.get("mcpServers")
                if servers is not None and not isinstance(servers, dict):
                    return False
            return True
        mcp_servers = data.get("mcpServers")
        if mcp_servers is not None and not isinstance(mcp_servers, dict):
            return False
        projects = data.get("projects")
        return projects is None or isinstance(projects, dict)

    @staticmethod
    def _supported_hook_settings_shape(data: object) -> bool:
        if data is None:
            return True
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks")
        return hooks is None or isinstance(hooks, dict)

    @classmethod
    def _build_server_entry(cls, command_args: list[str]) -> dict[str, object]:
        return {"type": "stdio", "command": "mempalace-mcp", "args": command_args, "env": {}}

    @classmethod
    def _build_hook_handler(cls, event: str, hook_name: str) -> dict[str, object]:
        return {
            "type": "command",
            "name": f"mempalace-{hook_name}",
            "command": f"mempalace hook run --hook {hook_name} --harness claude-code",
            "description": f"MemPalace {event} hook",
        }

    @classmethod
    def _build_hook_group(cls, event: str, hook_name: str) -> dict[str, object]:
        return {"hooks": [cls._build_hook_handler(event, hook_name)]}

    @staticmethod
    def _servers_match(existing: Optional[dict[str, object]], desired: dict[str, object]) -> bool:
        if not isinstance(existing, dict):
            return False
        return (
            existing.get("type") == desired["type"]
            and existing.get("command") == desired["command"]
            and list(existing.get("args", [])) == list(desired["args"])
        )

    @staticmethod
    def _with_server(existing: dict[str, object], server: dict[str, object]) -> dict[str, object]:
        updated = dict(existing)
        updated["mempalace"] = server
        return updated

    @staticmethod
    def _desired_args(palace) -> list[str]:
        if not palace:
            return []
        return ["--palace", str(Path(palace).expanduser())]

    @staticmethod
    def _validate_json_file(path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Expected JSON object")

    def _verify_target_registration(self, path: Path, action: IntegrationAction) -> None:
        payload = self._load_json(path)
        data = payload["data"]
        if payload["invalid"] or not self._supported_json_shape(data, action.effective_scope):
            raise RuntimeError(
                f"Claude {action.effective_scope} config did not verify after write"
            )

        target_server = self._target_server(data, action.effective_scope)
        desired_server = self._build_server_entry(list(action.command_args))
        if action.kind == "remove":
            if target_server is not None:
                raise RuntimeError(
                    f"Claude {action.effective_scope} config still reports mempalace after remove"
                )
            return
        if not self._servers_match(target_server, desired_server):
            raise RuntimeError(
                f"Claude {action.effective_scope} config did not verify mempalace after write"
            )

    @classmethod
    def _has_any_mempalace_hooks(cls, data: object) -> bool:
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return False
        for event in hooks:
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
            handlers = group.get("hooks")
            if handlers != [cls._build_hook_handler(event, hook_name)]:
                return False
        return True

    @classmethod
    def _find_hook_group(cls, hooks: dict[str, object], event: str) -> Optional[dict[str, object]]:
        definitions = hooks.get(event)
        if not isinstance(definitions, list):
            return None
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            handlers = definition.get("hooks")
            if not isinstance(handlers, list):
                continue
            for _, hook_name in cls._HOOK_EVENTS:
                expected_name = f"mempalace-{hook_name}"
                expected_command = f"mempalace hook run --hook {hook_name} --harness claude-code"
                for handler in handlers:
                    if not isinstance(handler, dict):
                        continue
                    if handler.get("name") == expected_name or handler.get("command") == expected_command:
                        return definition
        return None

    @classmethod
    def _strip_mempalace_handlers(cls, definitions: object) -> list[dict[str, object]]:
        if not isinstance(definitions, list):
            return []
        cleaned: list[dict[str, object]] = []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            handlers = definition.get("hooks")
            if not isinstance(handlers, list):
                cleaned.append(definition)
                continue
            kept_handlers = [handler for handler in handlers if not cls._is_mempalace_handler(handler)]
            if kept_handlers:
                updated = dict(definition)
                updated["hooks"] = kept_handlers
                cleaned.append(updated)
        return cleaned

    @classmethod
    def _is_mempalace_handler(cls, handler: object) -> bool:
        if not isinstance(handler, dict):
            return False
        name = handler.get("name")
        command = handler.get("command")
        for _, hook_name in cls._HOOK_EVENTS:
            expected_name = f"mempalace-{hook_name}"
            expected_command = f"mempalace hook run --hook {hook_name} --harness claude-code"
            if name == expected_name or command == expected_command:
                return True
        return False

    @classmethod
    def _upsert_hooks(cls, data: dict[str, object]) -> dict[str, object]:
        updated = dict(data)
        hooks = updated.get("hooks")
        hooks_dict = dict(hooks) if isinstance(hooks, dict) else {}
        for event, hook_name in cls._HOOK_EVENTS:
            definitions = cls._strip_mempalace_handlers(hooks_dict.get(event))
            definitions.append(cls._build_hook_group(event, hook_name))
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

    def _verify_target_hooks(self, path: Path, action: IntegrationAction) -> None:
        payload = self._load_json(path)
        data = payload["data"]
        if payload["invalid"] or not self._supported_hook_settings_shape(data):
            raise RuntimeError(
                f"Claude {action.effective_scope} settings did not verify after write"
            )
        if action.operation == "remove":
            if self._has_any_mempalace_hooks(data):
                raise RuntimeError(
                    f"Claude {action.effective_scope} settings still report mempalace hooks after remove"
                )
            return
        if not self._hooks_match(data):
            raise RuntimeError(
                f"Claude {action.effective_scope} settings did not verify mempalace hooks after write"
            )
