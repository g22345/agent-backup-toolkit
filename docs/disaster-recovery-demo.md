# Disaster-recovery demo

Run this drill before using real data and repeat it after security-sensitive
changes or destination migrations.

## Automated synthetic drill

Prerequisites: Python environment installed from the repository, `age`, and
`age-keygen` on `PATH`.

```bash
.venv/bin/python scripts/demo_local.py
```

Expected stages:

1. create a temporary synthetic workspace and temporary age identity;
2. back up one synthetic instruction file to a temporary local destination;
3. read back and verify the final receipt and encrypted artifact;
4. decrypt and verify archive plus manifest;
5. preview restore and confirm the target does not exist;
6. apply add-only restore and compare restored content;
7. delete the temporary drill directory automatically on normal exit.

The script prints `synthetic disaster-recovery drill: PASS` only after every
stage succeeds. It never reads normal agent directories or provider credentials.

## Manual remote drill

Use only a disposable private repository or test bucket with synthetic content.
Confirm destination visibility/permissions, run `doctor`, `backup`, `verify`, and
preview restore. Inspect the destination: it should contain only `.age` and
sanitized `.json` objects. Do not run a live-provider drill from automated CI.

