# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use the
repository's **Security → Report a vulnerability** flow so the report can be
handled privately. If that flow is unavailable, contact the maintainer through
their verified GitHub profile without including exploit details in public.

Include the affected version/commit, platform, minimal synthetic reproduction,
security impact, and whether any credentials or real data were involved. Never
attach a real private identity, credential, customer file, or backup artifact.

No response or remediation SLA is promised during the alpha stage.

## Supported versions

The project is pre-release. Only the latest commit and latest published alpha,
when one exists, receive security fixes.

## Security boundaries

The intended guarantees and known limitations are documented in
[`docs/security-model.md`](docs/security-model.md). In particular:

- encryption confidentiality depends on `age`, correct key custody, and a trusted local machine;
- v0.1 does not sign receipts or authenticate the creator of a ciphertext;
- a compromised operator account can access plaintext sources and the private identity;
- remote-provider availability and durability are outside this tool's control;
- a successful backup is not a substitute for periodically testing restore.

