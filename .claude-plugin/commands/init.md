---
description: Set up MemPalace — prefer uv tool install and the integration manager, then fall back to the legacy Claude plugin flow only if needed.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

Preferred path:

1. Install the package with `uv tool install --python 3.13 --editable /path/to/mempalace` or `uv tool install --python 3.13 --editable .` from the repo root.
2. Run `mempalace integrate claude --write` to configure Claude Code.
3. Use the generic `mempalace` skill with `init` if you still need palace initialization or repair.

Legacy fallback:

If you are already using the Claude plugin marketplace or a local plugin clone, keep the plugin as a fallback path and run `/mempalace:init` only for plugin-oriented repair or legacy setup flows.
