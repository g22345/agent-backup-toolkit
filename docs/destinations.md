# Destinations

Every destination receives only the encrypted `.tar.gz.age` artifact and
sanitized JSON receipts. Plaintext archives, config files, logs, raw paths, and
secret-scan excerpts are never destination objects.

## Local directory

```yaml
destination:
  type: local
  path: ~/agent-backups
```

Objects are created immutably with protected permissions. Complete local
read-back is hashed before success.

## Private GitHub Releases

Prerequisites: an existing **private** repository and authenticated `gh` CLI.

```yaml
destination:
  type: github
  repository: owner/private-backups
  tag_prefix: agent-backup
```

`doctor` checks repository visibility live. Public repositories are rejected.
Each backup uses one release; assets are uploaded without `--clobber`, downloaded
again, and verified. Repository names and command output are not written to
receipts.

## S3-compatible storage

Install the optional dependency:

```bash
uv pip install --python .venv/bin/python -e '.[s3]'
```

AWS S3:

```yaml
destination:
  type: s3
  bucket: private-agent-backups
  prefix: agent-backups
  region: ap-southeast-1
```

Cloudflare R2, Backblaze B2 S3 API, or MinIO can add `endpoint_url`:

```yaml
destination:
  type: s3
  bucket: private-agent-backups
  prefix: agent-backups
  endpoint_url: https://s3-compatible.example.invalid
```

Do not embed credentials in `endpoint_url`. Boto3's environment, profile, role,
or workload credential chain supplies authentication. Conditional writes prevent
replacement; a complete download and digest comparison is still mandatory.

Server-side encryption is welcome as defense in depth but never replaces the
client-side `age` envelope.

