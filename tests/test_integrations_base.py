import json
from pathlib import Path

import pytest

from mempalace.integrations.base import IntegrationAction
from mempalace.integrations.io import atomic_write_json, atomic_write_text, build_backup_path


def test_backup_path_contains_host_and_timestamp(tmp_path):
    target = tmp_path / "settings.json"

    backup_path = build_backup_path(target, host="claude", timestamp="20260410T120000Z")

    assert backup_path.parent == target.parent
    assert backup_path.name == "settings.claude.20260410T120000Z.bak.json"


def test_atomic_write_replaces_file_only_after_validation(tmp_path):
    target = tmp_path / "settings.txt"
    target.write_text("old", encoding="utf-8")

    seen = []

    def validator(path: Path) -> None:
        seen.append(path.read_text(encoding="utf-8"))
        assert path.read_text(encoding="utf-8") == "new"

    backup_path = atomic_write_text(
        target,
        "new",
        host="claude",
        timestamp="20260410T120000Z",
        validator=validator,
    )

    assert seen == ["new"]
    assert target.read_text(encoding="utf-8") == "new"
    assert backup_path == tmp_path / "settings.claude.20260410T120000Z.bak.txt"
    assert backup_path.read_text(encoding="utf-8") == "old"


def test_atomic_write_keeps_original_when_validator_fails(tmp_path):
    target = tmp_path / "settings.txt"
    target.write_text("old", encoding="utf-8")

    def validator(_path: Path) -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        atomic_write_text(
            target,
            "new",
            host="codex",
            timestamp="20260410T120000Z",
            validator=validator,
        )

    assert target.read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "settings.codex.20260410T120000Z.bak.txt").exists()


def test_plan_action_tracks_effective_and_requested_scope():
    action = IntegrationAction(
        host="gemini",
        kind="add",
        status="planned",
        summary="Add MemPalace MCP integration",
        path=Path("/tmp/settings.json"),
        effective_scope="project",
        shadowed_by="system",
        use_host_cli=True,
    )

    assert action.requested_scope == "auto"
    assert action.effective_scope == "project"
    assert action.shadowed_by == "system"
    assert action.use_host_cli is True
    assert action.backup_path is None


def test_json_write_preserves_unrelated_keys_semantically(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(
        '{"keep": "yes", "nested": {"a": 1}, "replace": "old"}',
        encoding="utf-8",
    )

    backup_path = atomic_write_json(
        target,
        {"replace": "new", "added": True},
        host="claude",
        timestamp="20260410T120000Z",
    )

    assert backup_path == tmp_path / "settings.claude.20260410T120000Z.bak.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "keep": "yes",
        "nested": {"a": 1},
        "replace": "new",
        "added": True,
    }
    assert json.loads(backup_path.read_text(encoding="utf-8")) == {
        "keep": "yes",
        "nested": {"a": 1},
        "replace": "old",
    }


def test_atomic_write_json_invalid_existing_json_raises_type_error(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(TypeError, match="Invalid JSON"):
        atomic_write_json(target, {"added": True}, host="codex")
