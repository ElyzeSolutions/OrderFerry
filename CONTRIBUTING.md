# Contributing

Thanks for helping improve OrderFerry.

1. Open an issue for substantial behavior or protocol changes.
2. Create a focused branch and keep unrelated changes out of the pull request.
3. Add or update tests for every behavior change.
4. Run the complete local verification suite before submitting:

   ```powershell
   uv sync --locked
   uv run ruff format --check src tests
   uv run ruff check src tests
   uv run python -m unittest discover -s tests -v
   uv build
   ```

Tests must not require a live broker account or place real orders. Use fake MT5
objects for deterministic coverage. Never commit account numbers, passwords,
tokens, terminal data directories, or logs.

Protocol changes should preserve the canonical `ok`/`result` and `ok`/`error`
response envelopes. New raw MT5 operations must be explicitly allowlisted;
never expose arbitrary attribute or method access.
