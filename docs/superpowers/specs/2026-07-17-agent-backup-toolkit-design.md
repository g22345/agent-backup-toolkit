# Agent Backup Toolkit Design

**Date:** 2026-07-17  
**Status:** Approved design; implementation not started  
**Working name:** `agent-backup-toolkit`  
**Intended maintainer:** `g22345`

## 1. Decision summary

Build a greenfield, open-source Python CLI for encrypted, fail-closed backup and disaster recovery of local AI-agent workspaces. The initial release is Codex-first without depending on any operator-specific business, customer, memory-bank, or runtime data.

The existing private backup control plane remains private. Its Git history, snapshots, receipts, paths, configuration, and backup artifacts must never be copied into or used as the history of this project. Reusable ideas may inform a new implementation, but every public file must be deliberately authored and reviewed in the new repository.

## 2. Product promise

> A fail-closed, encrypted, verifiable backup and disaster-recovery toolkit for local AI-agent workspaces.

The tool helps maintainers preserve durable instructions, skills, project context, knowledge files, and consistent SQLite snapshots without uploading plaintext or silently accepting an incomplete backup.

## 3. Target users and supported platforms

Primary users:

- open-source maintainers using Codex or similar local coding agents;
- developers who keep durable agent instructions, skills, and project context on local machines;
- small teams that need inspectable backup and restore workflows without adopting a hosted backup service.

Version 0.1 formally supports:

- macOS;
- Linux;
- Python 3.11, 3.12, and 3.13.

Windows is not a supported platform in version 0.1. The design must not deliberately block future Windows support.

## 4. OpenAI program alignment

The design responds to the official Codex for Open Source criteria checked on 2026-07-17:

- public repository and public GitHub profile;
- accurate primary/core maintainer role;
- meaningful usage, broad adoption, or clear software-ecosystem importance;
- repository usage and evidence of active maintenance;
- visible maintainer work such as issue triage, releases, review, and security upkeep;
- concrete Codex uses in pull-request review, maintainer automation, release workflows, or core OSS work.

Official sources:

- <https://openai.com/form/codex-for-oss/>
- <https://developers.openai.com/community/codex-for-oss>
- <https://learn.chatgpt.com/docs/codex-for-oss-terms>

The repository must be a real maintained tool, not an application prop. Application text must use only verifiable facts and current metrics. Selection is not guaranteed.

## 5. Version 0.1 scope

### 5.1 Commands

- `agent-backup init`: create a commented starter configuration without discovering or adding private paths automatically.
- `agent-backup doctor`: validate configuration, dependencies, encryption recipient, destination access, and safe local permissions without uploading a backup.
- `agent-backup backup`: run the complete fail-closed backup pipeline.
- `agent-backup verify`: download or read an existing encrypted artifact and verify its receipt, digest, encryption envelope, archive manifest, and file checksums.
- `agent-backup restore`: default to a no-write restore preview; require explicit flags for writes and overwrites.
- `agent-backup status`: read sanitized local receipts and report the most recent success or failure.

### 5.2 Supported source types

- individual regular files;
- directories with explicit inclusion and exclusion rules;
- SQLite databases captured through SQLite's backup API rather than copying a live database file;
- Codex-first presets for durable instructions and skills.

### 5.3 Explicitly excluded sources

Default presets and documentation must not collect:

- `auth.json` or equivalent authentication stores;
- API keys, tokens, cookies, passwords, or private keys;
- browser profiles;
- sessions, raw chat history, logs, caches, or temporary runtime state;
- sockets, device files, named pipes, or unknown binary formats;
- home-directory-wide or filesystem-wide sources;
- customer data or paths that were not deliberately selected by the operator.

Version 0.1 has no override that continues after a confirmed secret finding.

### 5.4 Supported destinations

- local directory, including an already-mounted NAS or external volume;
- private GitHub Releases;
- S3-compatible object storage, covering AWS S3, Cloudflare R2, Backblaze B2, and MinIO through one adapter.

Google Drive, Dropbox, hosted dashboards, and additional destination providers are outside version 0.1.

## 6. Technology choices

- Python 3.11+ and `pyproject.toml` packaging;
- Typer for the command-line interface;
- Pydantic for strict configuration validation;
- PyYAML for a readable configuration file;
- `age` as the mature external encryption implementation;
- local `gh` authentication and CLI for GitHub Releases so the project does not store GitHub tokens;
- `boto3` as an optional `s3` package extra;
- pytest, Ruff, and mypy for test, lint, and type-check workflows.

Encrypted backup artifacts use a documented `.tar.gz.age` envelope. The plaintext tar archive may exist only inside a protected temporary staging directory during one run.

## 7. Architecture and component boundaries

The package uses small modules with explicit interfaces:

- `config`: schema, safe defaults, logical source names, and validation;
- `collectors`: regular-file, directory, and consistent SQLite collection;
- `policy`: path boundaries, file-type policy, limits, exclusions, and secret scanning;
- `manifest`: canonical file metadata and SHA-256 checksums;
- `archive`: deterministic archive creation and `age` encryption/decryption;
- `destinations`: local, GitHub Releases, and S3 adapters behind one protocol;
- `receipts`: sanitized success/failure evidence without raw source paths;
- `restore`: preview, collision detection, encrypted rollback creation, and apply;
- `cli`: user-facing commands and stable exit-code mapping;
- `presets`: conservative Codex-first sources that contain durable text only.

Collectors never upload. Destinations never decide what is safe to collect. Restore never trusts archive paths before validating them. This separation is required for reviewability and future contributions.

## 8. Configuration contract

The generated YAML configuration contains:

- versioned schema number;
- a list of logically named sources;
- source type and explicit path;
- include/exclude patterns for directory sources;
- optional file-count, per-file-size, and total-size limits;
- one `age` recipient, referenced as a public recipient string;
- one destination configuration;
- a local state and receipt directory;
- local receipt-state location; version 0.1 performs no automatic deletion.

The configuration must not contain private encryption identities, GitHub tokens, S3 secret keys, passwords, or application data. Environment-variable references may name credentials but must not serialize their values.

## 9. Backup data flow

1. Parse and validate configuration before reading sources.
2. Create a mode-`0700` temporary staging directory.
3. Resolve each allowlisted source and enforce path, symlink, type, count, and size policy.
4. Capture files and consistent SQLite snapshots into staging.
5. Run high-confidence secret detection over eligible staged text.
6. Generate a canonical manifest containing logical source names, relative paths, sizes, modes, and SHA-256 digests.
7. Build the deterministic plaintext archive inside staging.
8. Encrypt with `age` to a separate temporary file.
9. Verify the encryption envelope can be parsed and record the encrypted artifact digest.
10. Upload or copy only the encrypted artifact and sanitized prepared receipt.
11. Read back the complete encrypted artifact and verify its SHA-256 digest.
12. Publish the sanitized final receipt, read it back, and verify that its identity and artifact fields match the prepared receipt.
13. Write the local success receipt only after both remote read-back checks pass.
14. Exit nonzero on any incomplete stage and write a sanitized local failure receipt.

No remote destination receives source files, plaintext archives, configuration files, logs, or secret-scan excerpts.

## 10. Restore data flow

1. Select a backup by explicit identifier.
2. Read and validate the sanitized receipt.
3. Download the encrypted artifact to protected temporary storage.
4. Verify its remote digest before decryption.
5. Decrypt and unpack only after rejecting absolute paths, `..` traversal, unsafe links, special files, and manifest mismatches.
6. Verify every restored file against the manifest.
7. Produce a preview of additions, collisions, and rejected paths; make no target writes by default.
8. Require `--apply` to create new files.
9. Require both `--apply` and `--overwrite` to replace existing files.
10. Before overwriting, create and verify a locally encrypted rollback archive using the configured `age` recipient.
11. Never delete target files as part of version 0.1 restore.

## 11. Security model

### 11.1 Core guarantees

- client-side encryption is mandatory for every destination;
- no `--no-encrypt` option;
- no remote plaintext fallback;
- no secret values in terminal output, logs, receipts, exceptions, or test fixtures;
- strict source allowlists rather than broad denylist discovery;
- destination upload is not success until complete read-back verification passes;
- remote GitHub repository visibility is checked live and must be private;
- S3 server-side encryption may be used but never replaces client-side `age` encryption.

### 11.2 Secret scanning

The scanner combines high-confidence credential formats, private-key markers, structured token patterns, and bounded entropy checks. Findings report only logical source, relative file, rule identifier, and line number where safe. They never print the matched value.

A confirmed finding blocks the run. Version 0.1 does not support ignore directives or policy bypasses.

### 11.3 Key handling

The tool accepts an `age` public recipient for backup and an operator-supplied identity path for verify/restore. It does not generate, upload, escrow, copy, or print private identities. Documentation explains that losing the identity makes the encrypted backup unrecoverable.

### 11.4 Threats explicitly tested

- source or archive path traversal;
- symlink escape and symlink swap;
- archive tampering;
- receipt/artifact mismatch;
- partial upload or truncated download;
- secret leakage through errors or debug output;
- unsafe file types;
- overwrite without explicit authorization;
- public GitHub destination misconfiguration;
- destination returning success without preserving the expected bytes.

## 12. Error handling and exit codes

Errors use typed categories with stable nonzero exit codes:

- `2`: invalid configuration or arguments;
- `3`: unsafe source or policy violation;
- `4`: secret detected;
- `5`: collection or SQLite snapshot failure;
- `6`: archive or encryption failure;
- `7`: destination authentication, upload, or read-back failure;
- `8`: verify failure;
- `9`: restore preview/apply/rollback failure.

User messages identify the failed stage and safe remediation without including sensitive content. A command interrupted before final receipt creation is never shown as successful by `status`.

## 13. Receipts and observable evidence

Sanitized receipts contain:

- schema and tool versions;
- random backup identifier and UTC timestamps;
- outcome and completed stage;
- logical source names, file count, and total byte count;
- destination type without bucket, repository, hostname, username, or local path;
- encrypted artifact filename, byte count, and SHA-256 digest;
- manifest digest and read-back verification result.

Receipts never contain raw source paths, filenames from private sources, hostnames, usernames, credentials, application content, or matched secrets.

## 14. Test and quality plan

### 14.1 Automated tests

- unit tests for config, path policy, secret rules, manifests, receipts, and exit-code mapping;
- collector tests for regular files, exclusions, limits, symlinks, and consistent SQLite snapshots;
- archive tests for deterministic manifests, tampering, invalid paths, and encryption subprocess handling;
- destination contract tests shared by local, GitHub, and S3 implementations;
- mocked GitHub and S3 failure tests;
- end-to-end local backup, verify, preview, restore, overwrite, and rollback tests using temporary fixtures;
- package-install and CLI smoke tests.

### 14.2 CI and repository checks

- GitHub Actions matrix for Ubuntu and macOS with Python 3.11-3.13;
- Ruff formatting/lint and mypy checks;
- dependency audit;
- CodeQL;
- repository secret scan;
- build and install the wheel before release.

CI tests use synthetic fixtures only. They may not import files from the private system or the operator's home directory.

## 15. Open-source repository contents

The first public-ready tree includes:

- `README.md` with problem statement, five-minute quickstart, screenshots or terminal examples, and restore drill;
- `LICENSE` using Apache-2.0;
- `SECURITY.md` with threat model and private vulnerability-reporting route;
- `CONTRIBUTING.md`;
- `CODE_OF_CONDUCT.md`;
- `CHANGELOG.md`;
- `ROADMAP.md`;
- architecture, configuration, destination, and disaster-recovery documentation;
- issue and pull-request templates;
- `docs/open-source-program-readiness.md` mapping current evidence to official criteria.

The public README must not claim adoption, security certification, compatibility, or production reliability that has not been demonstrated.

## 16. Release and adoption plan

1. Complete a local security review and secret scan before any remote is created.
2. Publish `v0.1.0` as an alpha release and PyPI package only after Matthew approves the exact public tree.
3. Run a real non-sensitive backup and restore drill and publish a reproducible demo using synthetic data.
4. Invite at least three genuine external testers across macOS and Linux.
5. Collect real feedback through GitHub issues; never manufacture activity, users, testimonials, or metrics.
6. Resolve material findings, document decisions, and publish `v0.2.0`.
7. Record only opt-in adopters in `ADOPTERS.md`; use public PyPI downloads, stars, forks, issues, releases, and contributions as evidence.
8. Prepare a Codex for Open Source application draft after the evidence exists.

The project includes no hidden telemetry. Adoption measurement uses public platform evidence and voluntary disclosure only.

## 17. Maintainer and application evidence

The application-readiness document tracks:

- maintainer role and repository control;
- the ecosystem problem and why reliable agent-state recovery matters;
- release history and maintenance cadence;
- issue triage, review, security, and release work;
- real usage and adoption evidence with dates and links;
- how Codex is used for review, tests, release preparation, and maintenance;
- a concrete, non-speculative API-credit use case;
- the exact metrics and facts used in each application answer.

The OpenAI Organization ID and personal application data remain outside the repository. The application is shown to Matthew in full and requires separate approval before submission.

## 18. Non-goals for version 0.1

- GUI or hosted dashboard;
- backup SaaS;
- Google Drive or Dropbox OAuth integrations;
- remote command execution or arbitrary pre-backup shell hooks;
- credential, cookie, browser-profile, or raw-session backup;
- automatic retention deletion;
- live PostgreSQL or other server-database export;
- Windows support;
- claiming universal support for every AI agent.

## 19. Main risks and mitigations

- **False confidence in backup:** require read-back verification and restore drills.
- **Secret leakage:** conservative allowlists, mandatory scan, encryption, and log redaction.
- **Private-history leakage:** use a new repository with a new Git history.
- **Overbroad product scope:** keep version 0.1 to three collectors and three destinations.
- **Low early adoption:** optimize installation and documentation, then recruit real testers before applying.
- **Application overclaiming:** maintain dated evidence and draft only from verified metrics.
- **External dependency failure:** `doctor` checks `age`, `gh`, credentials, and destination access before backup.

## 20. Acceptance criteria for implementation readiness

Implementation may start when:

- this design has no unresolved placeholders or contradictory requirements;
- Matthew approves the written spec;
- the implementation plan maps every version 0.1 requirement to files and tests;
- no private source file, snapshot, receipt, secret, or Git history has entered this repository.

Public release may start only when:

- all supported-platform CI checks pass;
- the local end-to-end restore drill passes;
- secret and provenance reviews report no private material;
- packaging and clean-machine installation are verified;
- Matthew reviews and explicitly approves the exact tree, repository name, license, description, and publish action.
