# Contributing

Contributions are welcome. Keep the agent contract predictable: domain rules belong in the service layer, CLI handlers stay thin, and successful commands emit one JSON value.

## Setup

```bash
uv sync
```

Run the complete local gate:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv build
```

Integration tests use a fake ComfyUI transport and do not require network access. The ffmpeg integration test skips explicitly when `ffmpeg` is unavailable.

## Development rules

- Add a failing behavioral test before implementation.
- Use project-relative paths in fixtures and examples.
- Never add production databases, credentials, renders, or assembly outputs.
- Do not weaken database constraints to make a service operation easier.
- Treat shot revisions and compilation manifests as immutable provenance.
- Add a migration rather than editing an already released migration.
- Keep external network and encoding work outside database transactions.

Pull requests should explain any schema migration, state-machine change, or CLI compatibility impact.
