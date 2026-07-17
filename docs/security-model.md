# Security model

## Protected assets

- confidentiality of allowlisted source content at the destination;
- integrity evidence for one immutable encrypted artifact and its manifest;
- operator control over what is collected and what restore may replace;
- secrecy of private identities and credential values from config, output, and receipts.

## Trust assumptions

- the local OS account, Python runtime, installed `age` binary, and private identity are trusted;
- the operator reviews every configured source and controls the restore target;
- SHA-256 and `age` behave as specified;
- provider credentials grant the minimum access required by the chosen destination.

## Fail-closed guarantees

- no plaintext destination mode exists;
- source collection is explicit and bounded;
- secret findings block the backup with no v0.1 bypass;
- archive members cannot be absolute, traverse with `..`, link, or use special file types;
- a provider upload response is not success without complete artifact read-back;
- local success is written only after final receipt read-back;
- restore preview performs no target writes;
- collision replacement requires `--apply --overwrite` and a decrypt-verified encrypted rollback;
- restore never deletes target files.

## Known limitations

- `age` encryption provides confidentiality and recipient authentication during
  decryption, but v0.1 does not sign receipts or authenticate who created a new
  ciphertext. Anyone with the public recipient can create another valid envelope.
- A destination or local attacker who can replace both artifacts and receipts may
  cause denial of service; sender signatures are future work.
- Secret scanning is intentionally high-confidence and cannot identify every secret.
- A compromised local account can read sources before encryption and use the identity.
- Multi-file restore is staged before commit but is not one filesystem-wide atomic transaction.
  An interruption may leave a partial add-only restore; encrypted rollback covers
  authorized collisions and is reported to the operator.
- Provider durability, retention, account recovery, billing, and availability are external.

## Key custody

The public recipient belongs in config. The private identity never belongs in
config, a destination, a repository, a receipt, or a backup source. Store it in a
separate protected recovery location. Loss of the identity is unrecoverable.

