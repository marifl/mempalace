"""Gemini MCP and hook adapter with scoped verification and safe JSON patching."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from .base import IntegrationAction
from .io import atomic_write_text


class GeminiAdapter:
    name = "gemini"
    _HOOK_NAME = "mempalace-precompress"
    _HOOK_DESCRIPTION = "Save MemPalace context before compression"

    def __init__(
        self,
        *,
        home_dir: Path | None = None,
        project_root: Path | None = None,
        system_defaults_path: Path | None = None,
        system_settings_path: Path | None = None,
    ):
        self.home_dir = Path(home_dir).expanduser() if home_dir else Path.home()
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()
        self._system_defaults_path = (
            Path(system_defaults_path)
            if system_defaults_path
            else self._default_system_defaults_path()
        )
        self._system_settings_path = (
            Path(system_settings_path)
            if system_settings_path
            else self._default_system_settings_path()
        )

    @property
    def user_config_path(self) -> Path:
        return self.home_dir / ".gemini" / "settings.json"

    @property
    def project_config_path(self) -> Path:
        return self.project_root / ".gemini" / "settings.json"

    @property
    def system_defaults_path(self) -> Path:
        return self._system_defaults_path

    @property
    def system_settings_path(self) -> Path:
        return self._system_settings_path

    @classmethod
    def _system_config_dir(cls) -> Path:
        if os.name == "nt":
            return Path("C:/ProgramData/gemini-cli")
        if os.uname().sysname == "Darwin":  # pragma: no cover - macOS path only
            return Path("/Library/Application Support/GeminiCli")
        return Path("/etc/gemini-cli")

    @classmethod
    def _default_system_defaults_path(cls) -> Path:
        override = os.environ.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH")
        if override:
            return Path(override)
        return cls._system_config_dir() / "system-defaults.json"

    @classmethod
    def _default_system_settings_path(cls) -> Path:
        override = os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
        if override:
            return Path(override)
        return cls._system_config_dir() / "settings.json"

    def discover(self) -> dict[str, object]:
        user_payload = self._load_json(self.user_config_path)
        project_payload = self._load_json(self.project_config_path)
        system_defaults_payload = self._load_json(self.system_defaults_path)
        system_payload = self._load_json(self.system_settings_path)

        return {
            "cli_available": bool(shutil.which("gemini")),
            "user_config_path": self.user_config_path,
            "project_config_path": self.project_config_path,
            "system_defaults_path": self.system_defaults_path,
            "system_settings_path": self.system_settings_path,
            "user_config_exists": self.user_config_path.exists(),
            "project_config_exists": self.project_config_path.exists(),
            "system_defaults_exists": self.system_defaults_path.exists(),
            "system_settings_exists": self.system_settings_path.exists(),
            "user_has_mempalace": self._extract_server(user_payload["data"]) is not None,
            "project_has_mempalace": self._extract_server(project_payload["data"]) is not None,
            "system_defaults_has_mempalace": self._extract_server(system_defaults_payload["data"])
            is not None,
            "system_has_mempalace": self._extract_server(system_payload["data"]) is not None,
            "user_has_precompress_hook": self._find_mempalace_hook(user_payload["data"]) is not None,
            "project_has_precompress_hook": self._find_mempalace_hook(project_payload["data"])
            is not None,
            "system_defaults_has_precompress_hook": self._find_mempalace_hook(
                system_defaults_payload["data"]
            )
            is not None,
            "system_has_precompress_hook": self._find_mempalace_hook(system_payload["data"])
            is not None,
            "user_invalid": user_payload["invalid"],
            "project_invalid": project_payload["invalid"],
            "system_defaults_invalid": system_defaults_payload["invalid"],
            "system_invalid": system_payload["invalid"],
        }

    def detect(self) -> bool:
        layers = self.discover()
        return bool(
            layers["cli_available"]
            or layers["user_config_exists"]
            or layers["project_config_exists"]
            or layers["system_defaults_exists"]
            or layers["system_settings_exists"]
        )

    def plan(self, *, palace=None, scope="auto", remove=False):
        if scope not in {"auto", "user", "project"}:
            summary = "Gemini integration supports only auto/project/user scope in Phase 1"
            return [
                IntegrationAction(
                    host=self.name,
                    kind="mcp",
                    status="cannot_apply",
                    summary=summary,
                    requested_scope=scope,
                ),
                IntegrationAction(
                    host=self.name,
                    kind="hook",
                    status="cannot_apply",
                    summary=summary,
                    requested_scope=scope,
                ),
            ]

        layers = self.discover()
        desired_args = tuple(self._desired_args(palace))
        target_scope = self._resolve_target_scope(scope, layers)
        target_path = self._target_path(target_scope)
        target_payload = self._load_json(target_path)
        system_payload = self._load_json(self.system_settings_path)

        system_server = self._extract_server(system_payload["data"])
        system_hook = self._find_mempalace_hook(system_payload["data"])

        return [
            self._plan_mcp_action(
                requested_scope=scope,
                target_scope=target_scope,
                target_path=target_path,
                target_payload=target_payload,
                system_server=system_server,
                desired_args=desired_args,
                remove=remove,
                cli_available=bool(layers["cli_available"]),
            ),
            self._plan_hook_action(
                requested_scope=scope,
                target_scope=target_scope,
                target_path=target_path,
                target_payload=target_payload,
                system_hook=system_hook,
                remove=remove,
            ),
        ]

    def apply(self, action: IntegrationAction) -> IntegrationAction:
        if action.status in {"skip", "cannot_apply"}:
            return action
        if action.kind in {"mcp", "remove"}:
            if action.use_host_cli:
                return self._apply_mcp_with_cli(action)
            return self._apply_mcp_with_file(action)
        if action.kind == "hook":
            return self._apply_hook_with_file(action)
        raise RuntimeError(f"Unsupported Gemini action kind: {action.kind}")

    def _plan_mcp_action(
        self,
        *,
        requested_scope: str,
        target_scope: str,
        target_path: Path,
        target_payload: dict[str, object],
        system_server: dict[str, object] | None,
        desired_args: tuple[str, ...],
        remove: bool,
        cli_available: bool,
    ) -> IntegrationAction:
        desired_server = self._build_server_entry(list(desired_args))

        if system_server is not None:
            if remove:
                return IntegrationAction(
                    host=self.name,
                    kind="remove",
                    status="cannot_apply",
                    summary="Gemini system settings define mempalace; Phase 1 will not mutate them",
                    path=self.system_settings_path,
                    requested_scope=requested_scope,
                    effective_scope="system",
                    shadowed_by="system",
                )
            if self._servers_match(system_server, desired_server):
                return IntegrationAction(
                    host=self.name,
                    kind="mcp",
                    status="skip",
                    summary="Gemini system settings already define MemPalace MCP registration",
                    path=self.system_settings_path,
                    requested_scope=requested_scope,
                    effective_scope="system",
                    shadowed_by="system",
                )
            return IntegrationAction(
                host=self.name,
                kind="mcp",
                status="cannot_apply",
                summary="Gemini system settings shadow MemPalace MCP registration",
                path=self.system_settings_path,
                requested_scope=requested_scope,
                effective_scope="system",
                shadowed_by="system",
                command_args=desired_args,
            )

        if target_payload["invalid"] and not cli_available:
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=f"Gemini {target_scope} settings are invalid JSON; refusing fallback write",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                command_args=desired_args,
            )

        if not cli_available and not self._supported_json_shape(target_payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="remove" if remove else "mcp",
                status="cannot_apply",
                summary=f"Gemini {target_scope} settings shape is unsupported for fallback write",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                command_args=desired_args,
            )

        existing = self._extract_server(target_payload["data"])
        use_host_cli = cli_available
        path = None if use_host_cli else target_path

        if remove:
            return IntegrationAction(
                host=self.name,
                kind="remove",
                status="update" if existing else "skip",
                summary=(
                    "Remove MemPalace MCP registration"
                    if existing
                    else "MemPalace MCP registration not present"
                ),
                path=path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=use_host_cli,
                command_args=desired_args,
            )

        if self._servers_match(existing, desired_server):
            return IntegrationAction(
                host=self.name,
                kind="mcp",
                status="skip",
                summary="MemPalace MCP registration already present",
                path=path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
                use_host_cli=use_host_cli,
                command_args=desired_args,
            )

        return IntegrationAction(
            host=self.name,
            kind="mcp",
            status="update" if existing else "create",
            summary="Update MemPalace MCP registration" if existing else "Add MemPalace MCP server",
            path=path,
            requested_scope=requested_scope,
            effective_scope=target_scope,
            use_host_cli=use_host_cli,
            command_args=desired_args,
        )

    def _plan_hook_action(
        self,
        *,
        requested_scope: str,
        target_scope: str,
        target_path: Path,
        target_payload: dict[str, object],
        system_hook: dict[str, object] | None,
        remove: bool,
    ) -> IntegrationAction:
        if system_hook is not None:
            if remove:
                return IntegrationAction(
                    host=self.name,
                    kind="hook",
                    status="cannot_apply",
                    summary="Gemini system settings define MemPalace PreCompress hook; Phase 1 will not mutate them",
                    path=self.system_settings_path,
                    requested_scope=requested_scope,
                    effective_scope="system",
                    shadowed_by="system",
                )
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="skip",
                summary="Gemini system settings already define MemPalace PreCompress hook",
                path=self.system_settings_path,
                requested_scope=requested_scope,
                effective_scope="system",
                shadowed_by="system",
            )

        if target_payload["invalid"]:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary=f"Gemini {target_scope} settings are invalid JSON; cannot patch hooks",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        if not self._supported_json_shape(target_payload["data"]):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="cannot_apply",
                summary=f"Gemini {target_scope} settings shape is unsupported for hook patching",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        existing = self._find_mempalace_hook(target_payload["data"])
        if remove:
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="update" if existing else "skip",
                summary=(
                    "Remove MemPalace PreCompress hook"
                    if existing
                    else "MemPalace PreCompress hook not present"
                ),
                operation="remove",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        if existing is not None and self._hook_matches(existing):
            return IntegrationAction(
                host=self.name,
                kind="hook",
                status="skip",
                summary="MemPalace PreCompress hook already present",
                path=target_path,
                requested_scope=requested_scope,
                effective_scope=target_scope,
            )

        return IntegrationAction(
            host=self.name,
            kind="hook",
            status="update" if existing else "create",
            summary="Update MemPalace PreCompress hook" if existing else "Add MemPalace PreCompress hook",
            operation="upsert",
            path=target_path,
            requested_scope=requested_scope,
            effective_scope=target_scope,
        )

    def _apply_mcp_with_cli(self, action: IntegrationAction) -> IntegrationAction:
        if action.kind == "remove":
            command = ["gemini", "mcp", "remove", "--scope", action.effective_scope, "mempalace"]
        else:
            command = [
                "gemini",
                "mcp",
                "add",
                "--scope",
                action.effective_scope,
                "mempalace",
                "mempalace-mcp",
            ]
            command.extend(action.command_args)

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Gemini command failed"
            raise RuntimeError(detail)

        self._verify_target_mcp(self._target_path(action.effective_scope), action)
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary)

    def _apply_mcp_with_file(self, action: IntegrationAction) -> IntegrationAction:
        path = self._target_path(action.effective_scope)
        payload = self._load_json(path)
        data = payload["data"] if isinstance(payload["data"], dict) else {}
        updated = self._remove_mcp(data) if action.kind == "remove" else self._upsert_mcp(
            data,
            list(action.command_args),
        )
        backup_path = atomic_write_text(
            path,
            json.dumps(updated, indent=2) + "\n",
            host=self.name,
            validator=self._validate_json_file,
        )
        self._verify_target_mcp(path, action)
        summary = (
            "Removed MemPalace MCP registration"
            if action.kind == "remove"
            else "MemPalace MCP registration present"
        )
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _apply_hook_with_file(self, action: IntegrationAction) -> IntegrationAction:
        path = self._target_path(action.effective_scope)
        payload = self._load_json(path)
        data = payload["data"] if isinstance(payload["data"], dict) else {}
        if action.operation == "remove":
            updated = self._remove_hook(data)
        else:
            updated = self._upsert_hook(data)

        backup_path = atomic_write_text(
            path,
            json.dumps(updated, indent=2) + "\n",
            host=self.name,
            validator=self._validate_json_file,
        )
        self._verify_target_hook(path, action)
        summary = (
            "Removed MemPalace PreCompress hook"
            if action.operation == "remove"
            else "MemPalace PreCompress hook present"
        )
        return replace(action, status="skip", summary=summary, backup_path=backup_path)

    def _resolve_target_scope(self, scope: str, layers: dict[str, object]) -> str:
        if scope != "auto":
            return scope
        for candidate, exists_key in (("project", "project_config_exists"), ("user", "user_config_exists")):
            if not layers[exists_key]:
                continue
            payload = self._load_json(self._target_path(candidate))
            if payload["invalid"]:
                continue
            if self._supported_json_shape(payload["data"]):
                return candidate
        return "user"

    def _target_path(self, scope: str) -> Path:
        if scope == "project":
            return self.project_config_path
        if scope == "user":
            return self.user_config_path
        if scope == "system":
            return self.system_settings_path
        raise RuntimeError(f"Unsupported Gemini scope: {scope}")

    @staticmethod
    def _desired_args(palace) -> list[str]:
        if not palace:
            return []
        return ["--palace", str(Path(palace).expanduser())]

    def _build_hook_command(self) -> str:
        return "mempalace hook run --hook precompact --harness gemini"

    @staticmethod
    def _build_server_entry(args: list[str]) -> dict[str, object]:
        return {"command": "mempalace-mcp", "args": list(args)}

    def _build_hook_definition(self) -> dict[str, object]:
        return {
            "hooks": [
                {
                    "type": "command",
                    "name": self._HOOK_NAME,
                    "command": self._build_hook_command(),
                    "description": self._HOOK_DESCRIPTION,
                }
            ]
        }

    @classmethod
    def _servers_match(cls, existing: Any, desired: dict[str, object]) -> bool:
        if not isinstance(existing, dict):
            return False
        return existing.get("command") == desired["command"] and list(existing.get("args", [])) == list(
            desired["args"]
        )

    def _hook_matches(self, hook: dict[str, object]) -> bool:
        return hook.get("type") == "command" and hook.get("command") == self._build_hook_command()

    @staticmethod
    def _supported_json_shape(data: Any) -> bool:
        if data is None:
            return True
        if not isinstance(data, dict):
            return False
        mcp_servers = data.get("mcpServers")
        hooks = data.get("hooks")
        return (mcp_servers is None or isinstance(mcp_servers, dict)) and (
            hooks is None or isinstance(hooks, dict)
        )

    @staticmethod
    def _load_json(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"invalid": False, "data": {}}
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {"invalid": True, "data": None}
        return {"invalid": not isinstance(data, dict), "data": data}

    @staticmethod
    def _extract_server(data: Any) -> dict[str, object] | None:
        if not isinstance(data, dict):
            return None
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return None
        server = servers.get("mempalace")
        return server if isinstance(server, dict) else None

    def _find_mempalace_hook(self, data: Any) -> dict[str, object] | None:
        if not isinstance(data, dict):
            return None
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return None
        precompress = hooks.get("PreCompress")
        if not isinstance(precompress, list):
            return None
        for definition in precompress:
            if not isinstance(definition, dict):
                continue
            hook_entries = definition.get("hooks")
            if not isinstance(hook_entries, list):
                continue
            for hook in hook_entries:
                if isinstance(hook, dict) and (
                    hook.get("name") == self._HOOK_NAME or hook.get("command") == self._build_hook_command()
                ):
                    return hook
        return None

    def _upsert_mcp(self, data: dict[str, object], desired_args: list[str]) -> dict[str, object]:
        updated = deepcopy(data)
        servers = updated.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        else:
            servers = dict(servers)
        servers["mempalace"] = self._build_server_entry(desired_args)
        updated["mcpServers"] = servers
        return updated

    def _remove_mcp(self, data: dict[str, object]) -> dict[str, object]:
        updated = deepcopy(data)
        servers = updated.get("mcpServers")
        if isinstance(servers, dict) and "mempalace" in servers:
            servers = dict(servers)
            servers.pop("mempalace", None)
            if servers:
                updated["mcpServers"] = servers
            else:
                updated.pop("mcpServers", None)
        return updated

    def _upsert_hook(self, data: dict[str, object]) -> dict[str, object]:
        updated = deepcopy(data)
        hooks = updated.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        else:
            hooks = dict(hooks)

        definitions = hooks.get("PreCompress")
        normalized: list[dict[str, object]] = []
        if isinstance(definitions, list):
            for definition in definitions:
                if not isinstance(definition, dict):
                    continue
                hook_entries = definition.get("hooks")
                if not isinstance(hook_entries, list):
                    normalized.append(deepcopy(definition))
                    continue
                kept = [
                    deepcopy(hook)
                    for hook in hook_entries
                    if not (
                        isinstance(hook, dict)
                        and (
                            hook.get("name") == self._HOOK_NAME
                            or hook.get("command") == self._build_hook_command()
                        )
                    )
                ]
                if kept:
                    migrated = dict(definition)
                    migrated["hooks"] = kept
                    normalized.append(migrated)

        normalized.append(self._build_hook_definition())
        hooks["PreCompress"] = normalized
        updated["hooks"] = hooks
        return updated

    def _remove_hook(self, data: dict[str, object]) -> dict[str, object]:
        updated = deepcopy(data)
        hooks = updated.get("hooks")
        if not isinstance(hooks, dict):
            return updated

        definitions = hooks.get("PreCompress")
        if not isinstance(definitions, list):
            return updated

        normalized: list[dict[str, object]] = []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            hook_entries = definition.get("hooks")
            if not isinstance(hook_entries, list):
                normalized.append(deepcopy(definition))
                continue
            kept = [
                deepcopy(hook)
                for hook in hook_entries
                if not (
                    isinstance(hook, dict)
                    and (
                        hook.get("name") == self._HOOK_NAME
                        or hook.get("command") == self._build_hook_command()
                    )
                )
            ]
            if kept:
                migrated = dict(definition)
                migrated["hooks"] = kept
                normalized.append(migrated)

        hooks = dict(hooks)
        if normalized:
            hooks["PreCompress"] = normalized
        else:
            hooks.pop("PreCompress", None)

        if hooks:
            updated["hooks"] = hooks
        else:
            updated.pop("hooks", None)
        return updated

    @staticmethod
    def _validate_json_file(path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Gemini settings must be a JSON object")

    def _verify_target_mcp(self, path: Path, action: IntegrationAction) -> None:
        payload = self._load_json(path)
        if payload["invalid"] or not self._supported_json_shape(payload["data"]):
            raise RuntimeError(f"Gemini {action.effective_scope} settings are invalid after write")
        existing = self._extract_server(payload["data"])
        if action.kind == "remove":
            if existing is not None:
                raise RuntimeError(
                    f"Gemini {action.effective_scope} settings still contain mempalace after remove"
                )
            return
        desired = self._build_server_entry(list(action.command_args))
        if not self._servers_match(existing, desired):
            raise RuntimeError(
                f"Gemini {action.effective_scope} settings did not verify mempalace after write"
            )

    def _verify_target_hook(self, path: Path, action: IntegrationAction) -> None:
        payload = self._load_json(path)
        if payload["invalid"] or not self._supported_json_shape(payload["data"]):
            raise RuntimeError(f"Gemini {action.effective_scope} settings are invalid after write")
        existing = self._find_mempalace_hook(payload["data"])
        if action.operation == "remove":
            if existing is not None:
                raise RuntimeError(
                    f"Gemini {action.effective_scope} settings still contain MemPalace hook after remove"
                )
            return
        if existing is None or not self._hook_matches(existing):
            raise RuntimeError(
                f"Gemini {action.effective_scope} settings did not verify MemPalace hook after write"
            )
