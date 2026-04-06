# Personal Projects

This is a personal projects repo — a collection of independent scripts and small apps.

## Code style

- Clean, readable, and maintainable code
- Use `ruff` for linting and formatting (`uv run ruff check` / `uv run ruff format`)
- Type checking with `pyright` (`uv run pyright`)

## Testing

No unit tests. Validate functionality end-to-end by running the scripts directly.

## Environment

Managed with `uv`. To install dependencies:

```bash
uv sync
```
