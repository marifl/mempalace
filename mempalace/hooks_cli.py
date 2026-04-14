"""
Hook logic for MemPalace — Python implementation of session-start, stop, and precompact hooks.

Reads JSON from stdin, outputs JSON to stdout.
Supported hooks: session-start, stop, precompact
Supported harnesses: claude-code, codex, gemini
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

import yaml

from mempalace.config import MempalaceConfig

SAVE_INTERVAL = 15
STATE_DIR = Path.home() / ".mempalace" / "hook_state"
AUTO_MINE_DEFAULT_TRIGGERS = {"stop", "precompact"}
AUTO_MINE_ALLOWED_TRIGGERS = set(AUTO_MINE_DEFAULT_TRIGGERS)
AUTO_MINE_MODE_MAP = {
    "off": set(),
    "stop": {"stop"},
    "precompact": {"precompact"},
    "both": set(AUTO_MINE_DEFAULT_TRIGGERS),
}

STOP_BLOCK_REASON = (
    "AUTO-SAVE checkpoint. Save key topics, decisions, quotes, and code "
    "from this session to your memory system. Organize into appropriate "
    "categories. Use verbatim quotes where possible. Continue conversation "
    "after saving."
)

PRECOMPACT_BLOCK_REASON = (
    "COMPACTION IMMINENT. Save ALL topics, decisions, quotes, code, and "
    "important context from this session to your memory system. Be thorough "
    "\u2014 after compaction, detailed context will be lost. Organize into "
    "appropriate categories. Use verbatim quotes where possible. Save "
    "everything, then allow compaction to proceed."
)


def _sanitize_session_id(session_id: str) -> str:
    """Only allow alnum, dash, underscore to prevent path traversal."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", session_id)
    return sanitized or "unknown"


def _count_human_messages(transcript_path: str) -> int:
    """Count human messages in a JSONL transcript, skipping command-messages."""
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return 0
    count = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", {})
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            if "<command-message>" in content:
                                continue
                        elif isinstance(content, list):
                            text = " ".join(
                                b.get("text", "") for b in content if isinstance(b, dict)
                            )
                            if "<command-message>" in text:
                                continue
                        count += 1
                except (json.JSONDecodeError, AttributeError):
                    pass
    except OSError:
        return 0
    return count


def _log(message: str):
    """Append to hook state log file."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_path = STATE_DIR / "hook.log"
        timestamp = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _output(data: dict):
    """Print JSON to stdout with consistent formatting (pretty-printed)."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _normalize_trigger_values(raw: object) -> Optional[Set[str]]:
    if isinstance(raw, str):
        values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple, set)):
        values = []
        for item in raw:
            if not isinstance(item, str):
                return None
            token = item.strip().lower()
            if token:
                values.append(token)
    else:
        return None
    normalized = set(values)
    if not normalized.issubset(AUTO_MINE_ALLOWED_TRIGGERS):
        return None
    return normalized


def _normalize_auto_mine_policy(raw: object, *, source: str) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _log(f"AUTO-MINE: ignoring {source} policy with unsupported type")
        return {}

    normalized: dict[str, object] = {}

    if "enabled" in raw:
        enabled = raw.get("enabled")
        if isinstance(enabled, bool):
            normalized["enabled"] = enabled
        else:
            _log(f"AUTO-MINE: ignoring {source}.enabled with unsupported type")

    if "dir" in raw:
        directory = raw.get("dir")
        if isinstance(directory, str) and directory.strip():
            normalized["dir"] = str(Path(directory).expanduser())
        else:
            _log(f"AUTO-MINE: ignoring {source}.dir with unsupported value")

    if "triggers" in raw:
        triggers = _normalize_trigger_values(raw.get("triggers"))
        if triggers is None:
            _log(f"AUTO-MINE: ignoring {source}.triggers with unsupported value")
        else:
            normalized["triggers"] = triggers

    return normalized


def _load_project_auto_mine_policy(project_root: Path) -> tuple[dict[str, object], bool]:
    for name in ("mempalace.yaml", "mempal.yaml"):
        path = project_root / name
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            _log(f"AUTO-MINE: ignoring project policy in {path}: {exc}")
            return {}, False
        if not isinstance(data, dict):
            _log(f"AUTO-MINE: ignoring project policy in {path} with unsupported root type")
            return {}, False
        auto_mine = data.get("auto_mine")
        normalized = _normalize_auto_mine_policy(auto_mine, source=f"{name}:auto_mine")
        return normalized, bool(normalized)
    return {}, False


def _load_env_auto_mine_policy() -> dict[str, object]:
    policy: dict[str, object] = {}
    directory = os.environ.get("MEMPAL_DIR", "").strip()
    if directory:
        policy["dir"] = str(Path(directory).expanduser())

    mode = os.environ.get("MEMPAL_AUTO_MINE", "").strip().lower()
    if not mode:
        return policy

    if mode in AUTO_MINE_MODE_MAP:
        triggers = AUTO_MINE_MODE_MAP[mode]
    else:
        triggers = _normalize_trigger_values(mode)
        if triggers is None:
            _log(f"AUTO-MINE: ignoring invalid MEMPAL_AUTO_MINE value: {mode}")
            return policy

    policy["enabled"] = bool(triggers)
    policy["triggers"] = set(triggers)
    return policy


def _resolve_project_root(parsed: dict) -> Path:
    cwd = parsed.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_auto_mine_policy(parsed: dict) -> dict[str, object]:
    merged: dict[str, object] = {"enabled": None, "dir": None, "triggers": None}
    user_policy = _normalize_auto_mine_policy(MempalaceConfig().auto_mine, source="user auto_mine")
    project_root = _resolve_project_root(parsed)
    project_policy, project_present = _load_project_auto_mine_policy(project_root)
    env_policy = _load_env_auto_mine_policy()

    for layer in (user_policy, project_policy, env_policy):
        for key, value in layer.items():
            merged[key] = value

    triggers = merged["triggers"]
    enabled = merged["enabled"]
    if enabled is None:
        enabled = bool(triggers)
    if enabled and triggers is None:
        triggers = set(AUTO_MINE_DEFAULT_TRIGGERS)
    if not enabled:
        triggers = set()

    directory = merged["dir"]
    if enabled and project_present and "dir" not in env_policy:
        directory = project_policy.get("dir", str(project_root))

    return {
        "enabled": bool(enabled),
        "dir": directory,
        "triggers": set(triggers or set()),
    }


def _maybe_auto_ingest(trigger: str = "stop", parsed: Optional[dict] = None):
    """Run auto-mine for the configured trigger when an explicit policy enables it."""
    policy = _resolve_auto_mine_policy(parsed or {})
    if not policy["enabled"]:
        return
    if trigger not in policy["triggers"]:
        return

    mempal_dir = policy["dir"]
    if not isinstance(mempal_dir, str) or not mempal_dir.strip():
        _log(f"AUTO-MINE: trigger {trigger} enabled but no directory configured")
        return
    if not os.path.isdir(mempal_dir):
        _log(f"AUTO-MINE: trigger {trigger} directory does not exist: {mempal_dir}")
        return

    try:
        log_path = STATE_DIR / "hook.log"
        with open(log_path, "a") as log_f:
            if trigger == "stop":
                subprocess.Popen(
                    [sys.executable, "-m", "mempalace", "mine", mempal_dir],
                    stdout=log_f,
                    stderr=log_f,
                )
            else:
                subprocess.run(
                    [sys.executable, "-m", "mempalace", "mine", mempal_dir],
                    stdout=log_f,
                    stderr=log_f,
                    timeout=60,
                )
    except OSError:
        pass


SUPPORTED_HARNESSES = {"claude-code", "codex", "gemini"}


def _parse_harness_input(data: dict, harness: str) -> dict:
    """Parse stdin JSON according to the harness type."""
    if harness not in SUPPORTED_HARNESSES:
        print(f"Unknown harness: {harness}", file=sys.stderr)
        sys.exit(1)
    if harness == "gemini":
        return {
            "session_id": _sanitize_session_id(str(data.get("session_id", "unknown"))),
            "transcript_path": str(data.get("transcript_path", "")),
            "cwd": str(data.get("cwd", "")),
            "hook_event_name": str(data.get("hook_event_name", "")),
            "trigger": str(data.get("trigger", "")),
        }
    return {
        "session_id": _sanitize_session_id(str(data.get("session_id", "unknown"))),
        "stop_hook_active": data.get("stop_hook_active", False),
        "transcript_path": str(data.get("transcript_path", "")),
    }


def hook_stop(data: dict, harness: str):
    """Stop hook: block every N messages for auto-save."""
    parsed = _parse_harness_input(data, harness)
    session_id = parsed["session_id"]
    stop_hook_active = parsed.get("stop_hook_active", False)
    transcript_path = parsed.get("transcript_path", "")

    # If already in a save cycle, let through (infinite-loop prevention)
    if str(stop_hook_active).lower() in ("true", "1", "yes"):
        _output({})
        return

    # Count human messages
    exchange_count = _count_human_messages(transcript_path)

    # Track last save point
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    last_save_file = STATE_DIR / f"{session_id}_last_save"
    last_save = 0
    if last_save_file.is_file():
        try:
            last_save = int(last_save_file.read_text().strip())
        except (ValueError, OSError):
            last_save = 0

    since_last = exchange_count - last_save

    _log(f"Session {session_id}: {exchange_count} exchanges, {since_last} since last save")

    if since_last >= SAVE_INTERVAL and exchange_count > 0:
        # Update last save point
        try:
            last_save_file.write_text(str(exchange_count), encoding="utf-8")
        except OSError:
            pass

        _log(f"TRIGGERING SAVE at exchange {exchange_count}")

        # Optional: auto-ingest if MEMPAL_DIR is set
        _maybe_auto_ingest("stop", parsed)

        _output({"decision": "block", "reason": STOP_BLOCK_REASON})
    else:
        _output({})


def hook_session_start(data: dict, harness: str):
    """Session start hook: initialize session tracking state."""
    parsed = _parse_harness_input(data, harness)
    session_id = parsed["session_id"]

    _log(f"SESSION START for session {session_id}")

    # Initialize session state directory
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Pass through — no blocking on session start
    _output({})


def hook_precompact(data: dict, harness: str):
    """Precompact hook: always block with comprehensive save instruction."""
    parsed = _parse_harness_input(data, harness)
    session_id = parsed["session_id"]

    _log(f"PRE-COMPACT triggered for session {session_id}")

    # Optional: auto-ingest synchronously before compaction (so memories land first)
    _maybe_auto_ingest("precompact", parsed)

    if harness == "gemini":
        _output({"systemMessage": PRECOMPACT_BLOCK_REASON})
        return

    # Always block -- compaction = save everything
    _output({"decision": "block", "reason": PRECOMPACT_BLOCK_REASON})


def run_hook(hook_name: str, harness: str):
    """Main entry point: read stdin JSON, dispatch to hook handler."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        _log("WARNING: Failed to parse stdin JSON, proceeding with empty data")
        data = {}

    hooks = {
        "session-start": hook_session_start,
        "stop": hook_stop,
        "precompact": hook_precompact,
    }

    handler = hooks.get(hook_name)
    if handler is None:
        print(f"Unknown hook: {hook_name}", file=sys.stderr)
        sys.exit(1)

    handler(data, harness)
