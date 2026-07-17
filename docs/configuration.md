# Configuration reference

Configuration is YAML with `schema_version: 1`. Unknown fields are rejected.
Paths expand `~` and environment variables, but source roots may not be `/` or
the operator's home directory.

```yaml
schema_version: 1
age_recipient: AGE_RECIPIENT_HERE
state_dir: ~/.local/state/agent-backup-toolkit

sources:
  - type: file
    name: agent-instructions
    path: ~/workspace/AGENTS.md
    required: true
    limits:
      max_files: 1
      max_file_bytes: 10485760
      max_total_bytes: 10485760

  - type: directory
    name: durable-skills
    path: ~/.codex/skills
    include: ["**/*.md", "**/*.yaml", "**/*.yml"]
    exclude: ["**/.git/**", "**/__pycache__/**"]
    limits:
      max_files: 10000
      max_file_bytes: 10485760
      max_total_bytes: 536870912

  - type: sqlite
    name: durable-state
    path: ~/workspace/durable-state.sqlite3
    required: false

destination:
  type: local
  path: ~/agent-backups
```

## Sources

Logical `name` values use lowercase letters, digits, hyphens, or underscores and
must be unique. `required: false` permits a missing source; other policy failures
still block the run.

File and directory sources accept conservative UTF-8 text types. Directory
patterns are evaluated against POSIX-style relative paths. Any included symlink,
special file, unknown extension, NUL byte, invalid UTF-8, or limit violation
blocks the source.

SQLite sources must end in `.db`, `.sqlite`, or `.sqlite3`. They are copied with
`sqlite3.Connection.backup()` and the staged snapshot must pass `PRAGMA quick_check`.

## Credentials

Configuration must not contain a private `age` identity, GitHub token, S3 access
key, secret key, password, cookie, or application content. GitHub authentication
comes from `gh`; S3 authentication comes from boto3's standard credential chain.

## State

`state_dir` stores sanitized final/failure receipts and encrypted overwrite
rollbacks. v0.1 does not automatically delete receipts or rollbacks.

