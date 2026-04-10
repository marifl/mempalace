---
name: init
description: Initialize a new MemPalace with uv-first Codex integration-manager setup and legacy fallback support.
allowed-tools: Bash, Read, Write, Edit
---

# MemPalace Init

Preferred setup:

```bash
mempalace integrate codex --write
```

Install MemPalace as a `uv` tool first if needed:

```bash
uv tool install --python 3.13 --editable .
```

If you are using the repository-local `.codex-plugin` fallback, keep it in the project root and then run the same MemPalace CLI setup.

Then run the initialization instructions:

```bash
mempalace instructions init
```
