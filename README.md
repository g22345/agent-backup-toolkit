# agent-backup-toolkit

Security-first encrypted backup and disaster recovery for allowlisted local
AI-agent workspace data.

> **Status:** pre-release alpha. Review the threat model and test with synthetic
> data before trusting it with important files.

`agent-backup-toolkit` is a small Python CLI for maintainers who keep durable
agent instructions, skills, configuration, or SQLite state on their own
macOS/Linux machines. It collects only explicitly configured sources, blocks
likely secrets, encrypts every artifact with [`age`](https://age-encryption.org/),
and calls a backup successful only after complete artifact and receipt read-back.

## Why this exists

Local coding agents can accumulate useful, maintainable workspace data that is
not part of the application repository. Copying an entire home directory is too
broad; syncing live databases is unsafe; and an upload response alone is not
proof of a recoverable backup. This project provides a narrow, reviewable
alternative with an actual restore drill.

## Security defaults

- explicit file, directory, and SQLite source allowlists;
- conservative text-file policy for file/directory sources;
- no symlink following, special files, unknown binaries, or broad filesystem roots;
- bounded file count, per-file size, and total source size;
- high-confidence secret scanning with redacted findings and no bypass in v0.1;
- mandatory client-side `age` encryption for local and remote destinations;
- private-repository check before GitHub Releases use;
- immutable writes and full encrypted-artifact SHA-256 read-back;
- final success receipt published and read back before local success is recorded;
- preview-only restore by default;
- explicit `--apply --overwrite` plus a verified encrypted rollback before replacement;
- no telemetry and no collection discovery.

See [Security model](docs/security-model.md) for guarantees and non-guarantees.

## Supported scope

| Area | v0.1 support |
| --- | --- |
| Platforms | macOS and Linux; Python 3.11+ |
| Sources | regular text file, bounded text directory, SQLite backup API snapshot |
| Destinations | local directory, private GitHub Releases, S3-compatible storage |
| Encryption | mandatory external `age` binary |
| Restore | preview, add-only apply, explicit overwrite with encrypted rollback |

Authentication files, cookies, sessions, logs, caches, browser profiles, private
keys, and home-directory-wide sources are intentionally not presets.

## Five-minute source checkout

Prerequisites: Python 3.11+, `age`, and `age-keygen`. GitHub destinations also
need `gh`; S3 destinations need the `s3` optional dependency.

```bash
git clone https://github.com/g22345/agent-backup-toolkit.git
cd agent-backup-toolkit
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev,s3]'

age-keygen -o identity.txt
chmod 600 identity.txt
age-keygen -y identity.txt

.venv/bin/agent-backup init --config ./config.yaml
# Edit config.yaml: replace AGE_RECIPIENT_HERE and review every source/destination.
.venv/bin/agent-backup doctor --config ./config.yaml
.venv/bin/agent-backup backup --config ./config.yaml
```

`identity.txt` can decrypt the backup. Keep it outside this repository, protect
it separately, and never upload it with the backup. Losing it makes the backup
unrecoverable.

## Verify and restore

Use the backup identifier printed by `backup` or shown by `status`:

```bash
.venv/bin/agent-backup verify BACKUP_ID \
  --identity ./identity.txt --config ./config.yaml

# Preview only: no target files are created.
.venv/bin/agent-backup restore BACKUP_ID \
  --identity ./identity.txt --target ./restore-preview --config ./config.yaml

# Add new files. Collisions stop the whole apply.
.venv/bin/agent-backup restore BACKUP_ID \
  --identity ./identity.txt --target ./restore-preview \
  --apply --config ./config.yaml

# Replace collisions only after a local encrypted rollback is verified.
.venv/bin/agent-backup restore BACKUP_ID \
  --identity ./identity.txt --target ./restore-preview \
  --apply --overwrite --config ./config.yaml
```

Restore never deletes target files.

## Reproducible synthetic drill

The demo creates a temporary identity, synthetic source, encrypted local backup,
verification, preview, and applied restore. It does not use operator data.

```bash
.venv/bin/python scripts/demo_local.py
```

## Configuration and destinations

- [Configuration reference](docs/configuration.md)
- [Destination setup](docs/destinations.md)
- [Architecture](docs/architecture.md)
- [Disaster-recovery drill](docs/disaster-recovery-demo.md)

## Development

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
```

Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
repository [AGENTS.md](AGENTS.md) before changing security-sensitive behavior.

## Project status and claims

This repository does not claim certification, a security audit, production
reliability, external adoption, or guaranteed support. Current milestones are in
[ROADMAP.md](ROADMAP.md); genuine public users may add themselves to
[ADOPTERS.md](ADOPTERS.md).

Licensed under Apache-2.0.
