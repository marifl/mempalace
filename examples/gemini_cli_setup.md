# Gemini CLI Integration Guide

This guide sets up MemPalace for the [Gemini CLI](https://github.com/google/gemini-cli) using the current `uv`-first path.

## 1. Install MemPalace

Use the verified Python 3.13 tool install:

```bash
uv tool install --python 3.13 mempalace
```

If you are working from a local clone instead of PyPI, use:

```bash
uv tool install --python 3.13 --editable /path/to/mempalace
```

If your machine defaults to Python 3.14, keep the explicit `--python 3.13` flag.

## 2. Connect Gemini MCP

Let MemPalace plan the setup first:

```bash
mempalace integrate gemini --dry-run
mempalace integrate gemini --write
```

If you prefer to configure Gemini manually, use the stable MCP entrypoint:

```bash
gemini mcp add mempalace mempalace-mcp --scope user
```

## 3. Gemini Hook Behavior

Gemini’s `PreCompress` hook is advisory-only. It can remind Gemini to save memory before compression, but it does not block compression if the hook fails.

If you want automatic pre-compression saving, add a `PreCompress` hook that calls:

```bash
mempalace hook run --hook precompact --harness gemini
```

## 4. Practical Notes

- `mempalace-mcp` is the stable server entrypoint.
- `mempalace integrate gemini` is the preferred setup path.
- Use the manual `gemini mcp add ...` flow only if you need to wire things by hand.
- Keep the explicit Python 3.13 install on systems where Python 3.14 is the default.
