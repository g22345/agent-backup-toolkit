# Agent Backup Toolkit v0.1 Implementation Plan

> **Execution default:** Implement inline in the main task with checkpoints. Use subagent-driven execution only after the active `AGENTS` delegation gate is satisfied.

**Goal:** Deliver a clean, installable, security-first v0.1 CLI that can collect allowlisted agent-workspace data, block secrets, encrypt and verify backups, store them locally or through GitHub/S3 adapters, and restore them safely.

**Architecture:** Use a small Python package with strict Pydantic configuration, isolated collectors and destination adapters, a fail-closed orchestration layer, and typed errors mapped to stable CLI exit codes. All remote artifacts are encrypted with the external `age` binary; restore validates receipt, digest, archive paths, and manifest before preview or write.

**Tech Stack:** Python 3.11+, Typer, Pydantic, PyYAML, platformdirs, optional boto3, pytest, Ruff, mypy, GitHub Actions, `age`, and `gh`.

---

## Repository map

- `pyproject.toml`: package metadata, runtime/optional/dev dependencies, CLI entrypoint, Ruff/mypy/pytest settings.
- `src/agent_backup_toolkit/`: implementation package.
- `src/agent_backup_toolkit/collectors/`: regular-file, directory, and SQLite collectors.
- `src/agent_backup_toolkit/policy/`: path/type/limit checks and secret scanner.
- `src/agent_backup_toolkit/destinations/`: local, private GitHub Releases, and S3-compatible adapters.
- `src/agent_backup_toolkit/presets/`: conservative Codex-first preset.
- `tests/unit/`: deterministic component tests.
- `tests/integration/`: end-to-end local workflow and adapter contract tests.
- `docs/`: architecture, configuration, security, restore drill, destinations, and application-readiness evidence.
- `.github/`: CI, CodeQL, issue/PR templates, and dependency update configuration.
- `AGENTS.md`: repository-specific contributor and Codex safety instructions.

### Task 1: Package foundation, errors, config, and command shell

**Files:**

- Create `pyproject.toml`
- Create `src/agent_backup_toolkit/__init__.py`
- Create `src/agent_backup_toolkit/errors.py`
- Create `src/agent_backup_toolkit/config.py`
- Create `src/agent_backup_toolkit/state.py`
- Create `src/agent_backup_toolkit/cli.py`
- Create `src/agent_backup_toolkit/presets/codex.yaml`
- Create `tests/unit/test_config.py`
- Create `tests/unit/test_cli.py`

**Behaviour and checks:**

- Implement schema-versioned YAML config with strict unknown-field rejection, logical source names, three source types, one age recipient, one destination, and local state path.
- Reject credential values in config and refuse broad roots such as `/` or an unscoped home directory.
- Implement typed error categories with exit codes `2` through `9` from the approved spec.
- Add `init`, `doctor`, `status`, and placeholder-free command registration for `backup`, `verify`, and `restore`; unavailable workflow code must fail with a typed internal/config error rather than claiming success.
- `init` writes a commented safe example only when the target does not exist.
- Verify with `pytest tests/unit/test_config.py tests/unit/test_cli.py -q`, `ruff check .`, and `mypy src`.

### Task 2: Collection and policy enforcement

**Files:**

- Create `src/agent_backup_toolkit/models.py`
- Create `src/agent_backup_toolkit/collectors/base.py`
- Create `src/agent_backup_toolkit/collectors/files.py`
- Create `src/agent_backup_toolkit/collectors/sqlite.py`
- Create `src/agent_backup_toolkit/policy/paths.py`
- Create `src/agent_backup_toolkit/policy/limits.py`
- Create `src/agent_backup_toolkit/policy/secrets.py`
- Create `tests/unit/test_collectors.py`
- Create `tests/unit/test_path_policy.py`
- Create `tests/unit/test_secret_policy.py`

**Behaviour and checks:**

- Resolve sources without following escaping symlinks; reject traversal, special files, unknown binaries, unbounded roots, and limit breaches.
- Copy regular files and directories into mode-`0700` staging with logical-source-relative paths.
- Snapshot SQLite databases using `sqlite3.Connection.backup()` and validate the staged database with `PRAGMA quick_check`.
- Detect high-confidence credential formats, private-key markers, structured tokens, and bounded high-entropy assignments without returning matched values.
- Test symlink escape/swap resistance, special files, include/exclude rules, count/size limits, live SQLite consistency, redacted findings, and secret-free fixtures.
- Verify with `pytest tests/unit/test_collectors.py tests/unit/test_path_policy.py tests/unit/test_secret_policy.py -q`.

### Task 3: Manifest, archive, encryption, and receipts

**Files:**

- Create `src/agent_backup_toolkit/manifest.py`
- Create `src/agent_backup_toolkit/archive.py`
- Create `src/agent_backup_toolkit/encryption.py`
- Create `src/agent_backup_toolkit/receipts.py`
- Create `tests/unit/test_manifest.py`
- Create `tests/unit/test_archive.py`
- Create `tests/unit/test_encryption.py`
- Create `tests/unit/test_receipts.py`

**Behaviour and checks:**

- Produce canonical manifests with logical source, safe relative path, file size, normalized mode, and SHA-256 digest.
- Build deterministic `.tar.gz` archives without absolute paths, unsafe links, special files, usernames, group names, or source-root leakage.
- Wrap `age` through argument arrays without shell execution; validate dependency, recipient format, identity permissions, command timeouts, output existence, and nonempty encrypted bytes.
- Create prepared/final/failure receipts containing only approved fields; enforce prepared-to-final identity and artifact consistency.
- Verify deterministic manifests, tamper detection, malicious tar rejection, timeout/error redaction, receipt schema validation, and zero sensitive values in logs/errors.

### Task 4: Destination adapters and backup orchestration

**Files:**

- Create `src/agent_backup_toolkit/destinations/base.py`
- Create `src/agent_backup_toolkit/destinations/local.py`
- Create `src/agent_backup_toolkit/destinations/github.py`
- Create `src/agent_backup_toolkit/destinations/s3.py`
- Create `src/agent_backup_toolkit/orchestrator.py`
- Create `tests/unit/test_destinations.py`
- Create `tests/unit/test_orchestrator.py`
- Create `tests/integration/test_local_backup.py`

**Behaviour and checks:**

- Define one adapter contract for preflight, prepared upload, artifact read-back, final-receipt publish/read-back, fetch, and listing across local directory, private GitHub Releases, and S3-compatible destinations.
- Local adapter uses atomic same-filesystem replacement and complete SHA-256 read-back.
- GitHub adapter calls `gh` without shell interpolation, verifies the destination repository is private, creates one backup release, and verifies downloaded bytes and receipts.
- S3 adapter uses optional boto3, client-side encrypted bytes, explicit timeouts/retries, safe object keys, and complete downloaded digest verification.
- Orchestrator implements every approved backup stage and writes local success only after artifact and final-receipt read-back; incomplete runs remain failures.
- Verify adapters with shared contract tests, mocked remote failures, and an end-to-end local backup using synthetic data.

### Task 5: Verify and safe restore

**Files:**

- Create `src/agent_backup_toolkit/verify.py`
- Create `src/agent_backup_toolkit/restore.py`
- Create `tests/unit/test_verify.py`
- Create `tests/unit/test_restore.py`
- Create `tests/integration/test_local_restore.py`

**Behaviour and checks:**

- `verify` fetches receipt/artifact, checks encrypted digest, decrypts to protected temporary storage, rejects unsafe archive entries, and validates manifest plus every file digest.
- `restore` defaults to preview and reports additions/collisions/rejections without writing.
- `--apply` permits new files; `--apply --overwrite` permits replacements only after creating and verifying an encrypted rollback archive.
- Restore never deletes target files and never writes outside the explicit target root.
- Test tampered receipts/artifacts/manifests, traversal, unsafe links, missing identity, collisions, dry-run purity, apply, overwrite, rollback creation, interrupted writes, and rollback failure.

### Task 6: Public documentation and maintenance surfaces

**Files:**

- Create `README.md`
- Create `AGENTS.md`
- Create `LICENSE`
- Create `SECURITY.md`
- Create `CONTRIBUTING.md`
- Create `CODE_OF_CONDUCT.md`
- Create `CHANGELOG.md`
- Create `ROADMAP.md`
- Create `ADOPTERS.md`
- Create `docs/architecture.md`
- Create `docs/configuration.md`
- Create `docs/destinations.md`
- Create `docs/disaster-recovery-demo.md`
- Create `docs/open-source-program-readiness.md`
- Create `scripts/demo_local.py`
- Create `.github/ISSUE_TEMPLATE/bug.yml`
- Create `.github/ISSUE_TEMPLATE/feature.yml`
- Create `.github/pull_request_template.md`

**Behaviour and checks:**

- Document only implemented behaviour, exact dependencies, supported platforms, threat model, key-loss warning, no-telemetry policy, and safe synthetic examples.
- Publish the code under the approved Apache-2.0 license and preserve required notices.
- Explain project importance without claiming external adoption, certification, production reliability, or support that does not exist.
- Include a five-minute local quickstart and reproducible synthetic backup/verify/restore drill.
- Provide a noninteractive synthetic demo script used by both users and CI.
- Application-readiness page maps dated public evidence to official criteria and leaves metrics empty until public data exists.

### Task 7: CI, packaging, and public-tree audit

**Files:**

- Create `.github/workflows/ci.yml`
- Create `.github/workflows/codeql.yml`
- Create `.github/workflows/secret-scan.yml`
- Create `.github/dependabot.yml`
- Create `tests/test_public_tree.py`
- Create `scripts/audit_public_tree.py`

**Behaviour and checks:**

- Run Ubuntu/macOS Python 3.11-3.13 tests, Ruff, mypy, wheel build, clean-wheel install, CLI smoke, and local end-to-end drill.
- Add CodeQL, dependency review/audit, and repository secret scanning without write-capable release automation.
- Audit the public tree and Git history for private paths, business/customer markers, credentials, backup artifacts, snapshots, receipts, large binaries, and foreign provenance.
- Build with `python -m build`; install the wheel in a clean temporary virtual environment; run `agent-backup --help`, `doctor`, and the synthetic drill.
- Run the complete suite with `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy src`, and `python scripts/audit_public_tree.py --repo .`.

## Final acceptance checkpoint

- All automated checks pass on the local machine for available dependencies.
- Unsupported external checks are reported as unverified rather than passed.
- The working tree contains no private source history, snapshots, receipts, credentials, customer data, or operator-specific paths.
- No GitHub remote, public repository, PyPI package, release, application, or profile change occurs before the maintainer reviews and explicitly approves the exact public tree and action.
