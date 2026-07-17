# Contributing

Thank you for helping improve `agent-backup-toolkit`.

## Before coding

1. Read `AGENTS.md`, `SECURITY.md`, and `docs/security-model.md`.
2. Search existing issues and keep one change focused on one problem.
3. For behavior changes, describe the threat or failure mode before proposing the implementation.

## Development setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev,s3]'
```

Run the full local gate:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m build
.venv/bin/python scripts/audit_public_tree.py --repo .
```

## Test rules

- Use temporary directories and synthetic content.
- Mock GitHub and S3 operations; never use a contributor's live account in CI.
- Do not place key-shaped strings, real paths, provider responses, or private data in fixtures.
- Include negative tests for traversal, symlinks, tampering, partial reads, overwrite gates, and redaction when relevant.

## Pull requests

Explain the user-visible outcome, security impact, tests run, and documentation
changed. A pull request must not weaken a fail-closed guarantee merely to make a
provider or edge case pass.

