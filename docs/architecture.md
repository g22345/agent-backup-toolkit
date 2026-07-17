# Architecture

The package separates safety decisions from storage operations:

```text
config -> collectors -> policy -> manifest -> archive -> age encryption
                                                   |
                                                   v
                        destination publish -> full read-back -> final receipt

final receipt -> artifact fetch -> digest -> age decrypt -> safe extract
             -> manifest verify -> restore preview -> optional apply/rollback
```

## Boundaries

- `config.py` parses a strict schema and rejects unknown credential-like fields.
- `collectors/` reads only explicit regular-file, directory, or SQLite sources.
- `policy/` owns path, type, count, size, and secret decisions.
- `manifest.py` owns canonical metadata and SHA-256 digests.
- `archive.py` creates deterministic regular-file-only archives and validates members.
- `encryption.py` invokes the external `age` binary through argument arrays, never a shell.
- `destinations/` stores encrypted artifacts and sanitized receipts; it never selects sources.
- `orchestrator.py` records success only after artifact and final-receipt read-back.
- `verify.py` distrusts the receipt, artifact, archive, and manifest until each layer passes.
- `restore.py` previews first and owns collision, rollback, and write gates.

## Backup state machine

1. Validate configuration.
2. Destination preflight.
3. Collect allowlisted sources into mode-`0700` temporary storage.
4. Block secret findings.
5. Build canonical manifest and deterministic tar.gz.
6. Encrypt with one public `age` recipient.
7. Publish prepared receipt and encrypted artifact.
8. Download the complete artifact and compare size plus SHA-256.
9. Publish and read back the final receipt.
10. Write local success state.

Any typed failure before step 10 produces only sanitized local failure state. An
incomplete remote upload is never shown as a verified success by `status`.

## Restore state machine

1. Read and validate final receipt.
2. Fetch exact expected artifact size and verify SHA-256.
3. Decrypt inside protected temporary storage.
4. Reject traversal, links, special files, duplicates, unexpected roots, and limits.
5. Validate canonical manifest and every extracted file.
6. Preview additions, collisions, and rejected paths without creating the target.
7. With `--apply`, stage and read-back-check all replacement bytes.
8. With collisions and `--overwrite`, first create and decrypt-verify a local encrypted rollback.
9. Commit without deleting target files.

