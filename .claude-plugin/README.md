# MemPalace Claude Code Plugin

A Claude Code plugin that gives your AI a persistent memory system. Mine projects and conversations into a searchable palace backed by ChromaDB, with 19 MCP tools, auto-save hooks, and 5 guided skills.

## Prerequisites

- `uv` installed
- Python 3.13
- If you are on Python 3.14, pin the tool install to `--python 3.13` for the tested Chroma path

## Installation

### Primary Path

Install the package with `uv` first, then configure Claude with the integration manager:

```bash
uv tool install --python 3.13 --editable /path/to/mempalace
mempalace integrate claude --write
```

If you are already inside the repository, use `uv tool install --python 3.13 --editable .`.

## Legacy Fallback

The Claude Code marketplace/plugin flow still works as a fallback for legacy setups:

```bash
claude plugin marketplace add milla-jovovich/mempalace
claude plugin install --scope user mempalace
```

After installing the legacy plugin, run the init command to complete any remaining setup:

```bash
/mempalace:init
```

## Available Slash Commands

| Command | Description |
|---------|-------------|
| `/mempalace:help` | Show available tools, skills, and architecture |
| `/mempalace:init` | Set up MemPalace -- install, configure MCP, onboard |
| `/mempalace:search` | Search your memories across the palace |
| `/mempalace:mine` | Mine projects and conversations into the palace |
| `/mempalace:status` | Show palace overview -- wings, rooms, drawer counts |

## Hooks

MemPalace registers two hooks that run automatically:

- **Stop** -- Saves conversation context every 15 messages.
- **PreCompact** -- Preserves important memories before context compaction.

Auto-mine is disabled by default. Enable it explicitly with either:

- `~/.mempalace/config.json` -> `auto_mine`
- project-local `mempalace.yaml` -> `auto_mine`
- temporary env overrides: `MEMPAL_AUTO_MINE=stop|precompact|both|off` and optional `MEMPAL_DIR=/absolute/path`

`MEMPAL_DIR` alone does not enable auto-mine.

## MCP Server

The primary path configures the MCP server through `mempalace integrate claude --write`. The legacy plugin path still exposes the local MCP server and can be repaired with `/mempalace:init` when needed.

## Full Documentation

See the main [README](../README.md) for complete documentation, architecture details, and advanced usage.
