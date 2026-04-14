# MemPalace - Codex CLI Plugin

Give your AI a persistent memory -- mine projects and conversations into a searchable palace backed by ChromaDB, with 19 MCP tools, auto-save hooks, and guided skills.

## Prerequisites

- Python 3.13 explicitly recommended
- Codex CLI installed and configured
- `uv` installed

If your system defaults to Python 3.14, pin 3.13 explicitly with `uv tool install --python 3.13 ...`.

## Installation

### Preferred: global integration-manager setup

1. Install MemPalace as a `uv` tool from the repository root:

```bash
uv tool install --python 3.13 --editable .
```

2. Register Codex through the integration manager:

```bash
mempalace integrate codex --write
```

3. Verify Codex sees the plugin:

```bash
codex --plugins
```

4. Initialize your palace:

```bash
codex /init
```

### Repo-Local Fallback

If you want to keep the plugin directory in the repository and manage Codex integration manually, the legacy `.codex-plugin` layout still works as a fallback.

1. Copy or symlink the `.codex-plugin` directory into your project root:

```bash
cp -r .codex-plugin /path/to/your/project/.codex-plugin
```

2. Install MemPalace with `uv` from the repository root:

```bash
uv tool install --python 3.13 --editable .
```

3. Verify the plugin is detected:

```bash
codex --plugins
```

4. Initialize your palace:

```bash
codex /init
```

## Available Skills

| Skill | Description |
|-------|-------------|
| `/help` | Show available commands and usage tips |
| `/init` | Initialize a new memory palace |
| `/search` | Semantic search across all mined memories |
| `/mine` | Mine a project or conversation into your palace |
| `/status` | Show palace status, room counts, and health |

## Hooks

The plugin includes native Codex hooks for session start and stop. The stop hook triggers an auto-save checkpoint every 15 user messages and preserves conversation context into your palace.

Set the `MEMPAL_DIR` environment variable to a directory path to automatically run `mempalace mine` on that directory during each save trigger.

## Support

- Repository: https://github.com/milla-jovovich/mempalace
- Issues: https://github.com/milla-jovovich/mempalace/issues
