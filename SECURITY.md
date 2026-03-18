# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✓ active  |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report them privately using [GitHub's private vulnerability reporting](https://github.com/jefftrojan/robopotato/security/advisories/new).

Please include:

- A description of the vulnerability and its potential impact
- Step-by-step reproduction instructions
- The robopotato version affected (`/health` → `version` field)
- Any proof-of-concept code or HTTP requests

We aim to acknowledge reports within **72 hours** and provide an initial assessment within **7 days**.

## Scope

The following are in scope:

- Authentication bypass (forging or replaying tokens without the HMAC secret)
- Authorization bypass (accessing namespaces or capabilities beyond what a role permits)
- Denial-of-service via the HTTP API or WebSocket endpoint
- Information disclosure (reading state entries without the required capability)
- Dependency vulnerabilities with a known CVE that affect robopotato's behaviour

The following are **not** in scope:

- Issues requiring physical access to the server
- Attacks that require the HMAC secret to already be compromised (if your secret leaks, rotate it)
- `ROBOPOTATO_SECRET` brute-force (use a secret with sufficient entropy — see README)

## Cryptographic design notes

robopotato uses **HMAC-SHA256** for token signing with constant-time comparison on verification. If you believe there is a flaw in this design, please include a full cryptographic analysis in your report.
