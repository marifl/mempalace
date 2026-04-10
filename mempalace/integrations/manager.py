"""Shared orchestration for MemPalace host integrations."""

from __future__ import annotations

from dataclasses import replace

from .base import IntegrationAction
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter


def get_adapters():
    """Return host adapters for supported CLI integrations."""
    return [
        ClaudeAdapter(),
        CodexAdapter(),
        GeminiAdapter(),
    ]


def select_adapters(adapters, hosts):
    if hosts:
        by_name = {adapter.name: adapter for adapter in adapters}
        return [by_name[name] for name in hosts if name in by_name]
    return [adapter for adapter in adapters if adapter.detect()]


def build_plan(adapters, *, palace, scope, remove):
    plan = []
    for adapter in adapters:
        actions = adapter.plan(palace=palace, scope=scope, remove=remove)
        if isinstance(actions, IntegrationAction):
            actions = [actions]
        for action in actions:
            plan.append({"adapter": adapter, "action": action})
    return plan


def render_plan(plan):
    print("MemPalace integration plan:")
    if not plan:
        print("  No matching hosts detected.")
        return

    for entry in plan:
        action = entry["action"]
        mutation = "host-cli" if action.use_host_cli else "file-patch" if action.path else "none"
        effective_scope = action.effective_scope or "-"
        print(f"- {action.host}: {action.status} {action.summary}")
        print(
            f"  requested={action.requested_scope} effective={effective_scope} "
            f"mutation={mutation}"
        )
        if action.shadowed_by:
            print(f"  shadowed-by={action.shadowed_by}")
        if action.path:
            print(f"  path={action.path}")


def _confirm():
    answer = input("Apply these changes? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def apply_plan(plan):
    exit_code = 0
    for entry in plan:
        action = entry["action"]
        if action.status not in {"create", "update"}:
            continue
        try:
            updated = entry["adapter"].apply(action)
            if updated is not None:
                entry["action"] = updated
        except Exception as exc:
            exit_code = 1
            entry["action"] = replace(
                action,
                status="cannot_apply",
                summary=f"{action.summary} ({exc})",
            )
    return exit_code


def run_integrations(*, hosts, dry_run, write, palace, scope, remove):
    adapters = get_adapters()
    selected = select_adapters(adapters, hosts)
    plan = build_plan(selected, palace=palace, scope=scope, remove=remove)
    render_plan(plan)
    if not plan:
        return 0
    if dry_run:
        return 0
    if not write and not _confirm():
        return 1
    return apply_plan(plan)
