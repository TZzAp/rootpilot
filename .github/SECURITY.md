# Security policy

Please **don't open a public issue** for security problems. Use GitHub's private
[**Report a vulnerability**](../../security/advisories/new) form instead (Security tab).

Useful things to include: the device, iOS version and jailbreak, the RootPilot commit, what an
attacker controls (screen content, a local app, the network), and what they can achieve.

The threat model — prompt injection through screen content, local socket exposure, secrets, the
shell tool and the kill switch — is described in [`docs/SECURITY.md`](../docs/SECURITY.md).
