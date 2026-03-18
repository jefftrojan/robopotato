# Changelog

All notable changes to robopotato are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
robopotato uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- Token refresh endpoint (`POST /tokens/refresh`) — extend a valid token without re-registering
- Agent listing endpoint (`GET /agents`) — requires `agent_list` capability
- `ROBOPOTATO_MAX_AGENTS` config cap
- Prometheus metrics endpoint (`GET /metrics`)
- Docker image and `docker-compose.yml`

---

## [0.1.0] — 2026-03-18

Initial public release.

### Added

#### Core server
- axum 0.8 HTTP server with Tokio async runtime
- `GET /health` — liveness probe, returns service name and version
- `GET /events` — WebSocket endpoint streaming all state and lifecycle events

#### Authentication & authorization
- HMAC-SHA256 capability tokens: `base64url(claims_json).hex(hmac_sha256)`
- Three roles: `orchestrator`, `worker`, `observer`
- Nine capabilities: `state_read/write_{global,shared,own}`, `agent_list`, `agent_revoke`, `token_verify`
- Constant-time HMAC comparison (timing-attack resistant)
- Token expiry enforced on every request
- `POST /agents/register` — open registration, issues signed token
- `POST /tokens/verify` — verify token validity and revocation status (public endpoint)
- axum middleware that enforces Bearer tokens on all protected routes and injects `TokenClaims` as a request extension

#### State management
- In-memory `RwLock<HashMap>` key-value store
- Namespaced keys: `global.*`, `shared.*`, `agent.<id>.*`
- Namespace-level capability enforcement: workers cannot write `global.*` or other agents' namespaces; observers cannot write anywhere
- Optimistic Concurrency Control (OCC): optional `expected_version` field on writes; returns 409 Conflict on stale version
- `GET /state/{key}` — read with capability check
- `PUT /state/{key}` — write with capability + ownership check + OCC
- `DELETE /state/{key}` — delete with capability check
- `GET /state/namespace/{ns}` — list all keys under a namespace prefix

#### Agent lifecycle
- `DELETE /agents/{id}` — revoke an agent (orchestrator only); token immediately rejected on next request
- In-memory revocation set checked on every authenticated request

#### Event bus
- Tokio broadcast channel streaming typed events: `StateChanged`, `StateDeleted`, `AgentRegistered`, `AgentRevoked`
- WebSocket handler fans out events to all connected subscribers

#### Optional SQLite persistence (`--features persist`)
- `ROBOPOTATO_PERSIST=true` enables write-through persistence
- WAL journal mode for concurrent read performance
- `load_from_db()` recovers full state on startup
- Transparent to the HTTP API — no behavior change when disabled

#### Test suite
- 8 Rust unit tests: token sign/verify roundtrip, tampered token rejection, wrong secret, expiry, OCC conflict, version increment, namespace isolation
- 20 Python adversarial integration tests:
  - Expired token replay
  - Wrong-secret forgery
  - Payload tampering without re-signing (privilege escalation attempt)
  - Missing Authorization header on all protected routes
  - Cross-namespace pollution (worker → global/other-agent)
  - Observer write attempts
  - Mid-task agent revocation
  - OCC storm (5 concurrent writers, exactly 1 wins)
  - Malformed token formats (no dot, bad base64, not-JSON payload)
  - Public endpoint accessibility without auth
  - Version number sequence correctness
- Claude Code agent comparison harness (`compare.py`) — runs with/without robopotato side-by-side

#### Documentation
- `WHITEPAPER.md` — research context, threat model, design rationale, 40+ citations
- `README.md` — full API reference, quick start, capability matrix, token format
- `.env.example` — documented environment variables

[Unreleased]: https://github.com/trojan0x/robopotato/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/trojan0x/robopotato/releases/tag/v0.1.0
