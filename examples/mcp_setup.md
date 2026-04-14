# MCP Integration — Claude Code

## Setup

Install MemPalace as a uv tool:

```bash
uv tool install --python 3.13 mempalace
```

If you are working from a local clone instead of PyPI, use:

```bash
uv tool install --python 3.13 --editable /path/to/mempalace
```

Then let MemPalace wire Claude for you:

```bash
mempalace integrate claude --write
```

Or configure Claude manually:

Run the MCP server:

```bash
mempalace-mcp
```

Or add it to Claude Code:

```bash
claude mcp add mempalace -- mempalace-mcp
```

Keep the explicit `--python 3.13` when your machine defaults to Python 3.14.

## Available Tools

The server exposes the full MemPalace MCP toolset. Common entry points include:

- **mempalace_status** — palace stats (wings, rooms, drawer counts)
- **mempalace_search** — semantic search across all memories
- **mempalace_list_wings** — list all projects in the palace

## Usage in Claude Code

Once configured, Claude Code can search your memories directly during conversations.
