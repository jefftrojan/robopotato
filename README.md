# robopotato 🥔

**Lightweight inter-agent trust and shared state server for AI agent swarms.**

robopotato solves two hard problems that appear the moment you run more than one AI agent in the same environment:

1. **Who is allowed to do what?** — Agents receive cryptographically signed capability tokens. Every protected API call is verified; no agent can lie about its identity or escalate its own privileges.
2. **How do concurrent agents coordinate without stomping on each other?** — A namespaced key-value store with per-key version numbers lets agents perform optimistic-concurrency-controlled (OCC) writes, turning silent data races into explicit 409 Conflict errors.

A real-time WebSocket bus streams every state change and lifecycle event so orchestrators and observers always have a live view.

---

## Features

| Feature | Detail |
|---|---|
| HMAC-SHA256 capability tokens | Signed `base64url(json).hex(hmac)` tokens; constant-time verification; no JWT library dependency |
| Three roles | `orchestrator` · `worker` · `observer` with fine-grained capability checks |
| Nine capabilities | `state_read/write_{global,shared,own}` · `agent_list` · `agent_revoke` · `token_verify` |
| Namespaced KV store | `global.*` (orchestrator-only write) · `shared.*` (worker-writable) · `agent.<id>.*` (owner-only) |
| Optimistic Concurrency Control | Pass `expected_version` on writes; server returns 409 on stale reads |
| Agent revocation | Orchestrators can revoke any agent mid-flight; revoked tokens are rejected immediately |
| WebSocket event bus | Streams `StateChanged`, `StateDeleted`, `AgentRegistered`, `AgentRevoked` events |
| Optional SQLite persistence | Enable with `--features persist` + `ROBOPOTATO_PERSIST=true`; WAL mode, write-through, startup recovery |
| Framework-agnostic HTTP API | Plain JSON over HTTP — works with curl, Python httpx, any language |
| Self-contained | Single binary, no external infrastructure required |

---

## Quick start

### Prerequisites

- Rust 1.75+ (`rustup update stable`)
- Optional: Python 3.11+ with `pip install httpx` for the test suite

### Run

```bash
git clone https://github.com/trojan0x/robopotato
cd robopotato
ROBOPOTATO_SECRET=change-me cargo run --release
```

The server starts on `http://127.0.0.1:7878`.

### With SQLite persistence

```bash
ROBOPOTATO_SECRET=change-me \
ROBOPOTATO_PERSIST=true \
ROBOPOTATO_DB_PATH=./data/robopotato.db \
cargo run --release --features persist
```

State survives process restarts. The database is created automatically.

---

## Configuration

All configuration is via environment variables (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `ROBOPOTATO_SECRET` | **required** | HMAC signing secret — keep this private |
| `ROBOPOTATO_HOST` | `127.0.0.1` | Bind address |
| `ROBOPOTATO_PORT` | `7878` | Bind port |
| `ROBOPOTATO_TOKEN_TTL` | `3600` | Token lifetime in seconds |
| `ROBOPOTATO_PERSIST` | `false` | Enable SQLite write-through persistence |
| `ROBOPOTATO_DB_PATH` | `robopotato.db` | SQLite file path (requires `--features persist`) |

Copy `.env.example` to `.env` and fill in your secret.

---

## API reference

### Public endpoints (no auth required)

#### `GET /health`
```json
{ "status": "ok", "service": "robopotato", "version": "0.1.0" }
```

#### `POST /agents/register`
Register an agent and receive a signed capability token.

```jsonc
// Request
{ "role": "worker", "name": "optional-display-name" }

// Response
{
  "agent_id": "uuid-v4",
  "token": "base64payload.hexsig",
  "role": "worker",
  "expires_at": "2025-01-01T01:00:00+00:00"
}
```

Roles: `orchestrator` | `worker` | `observer`

#### `POST /tokens/verify`
Verify a token and check the revocation list (useful for agent-to-agent trust checks).

```jsonc
// Request
{ "token": "base64payload.hexsig" }

// Response (valid)
{ "valid": true, "agent_id": "...", "role": "...", "expires_at": "..." }

// Response (invalid)
{ "valid": false, "reason": "token expired" }
```

#### `GET /events` (WebSocket)
Upgrade to WebSocket to receive a live stream of all events.

```jsonc
// StateChanged
{ "type": "StateChanged", "key": "shared.task", "version": 3, "agent_id": "..." }

// AgentRevoked
{ "type": "AgentRevoked", "agent_id": "..." }
```

---

### Protected endpoints (Bearer token required)

All requests must include `Authorization: Bearer <token>`.

#### `GET /state/{key}`
Read a state entry. Workers can read `global.*` and `shared.*`; they can only read their own `agent.<id>.*`.

#### `PUT /state/{key}`
Write a state entry.

```jsonc
// Request
{ "value": <any JSON>, "expected_version": 2 }  // expected_version is optional (OCC)

// Response — 200 on success
{ "key": "shared.result", "value": ..., "version": 3, "owner_agent_id": "...", "updated_at": "..." }

// Response — 409 on OCC conflict
{ "error": "version conflict: expected 2, got 3" }
```

#### `DELETE /state/{key}`
Delete a state entry. Requires write capability for the key's namespace.

#### `GET /state/namespace/{ns}`
List all entries under a namespace prefix (`global`, `shared`, or an `agent.<id>`).

```jsonc
{ "entries": [ { "key": "...", "value": ..., "version": 1, ... } ] }
```

#### `DELETE /agents/{id}`
Revoke an agent. Requires `agent_revoke` capability (orchestrator only).

---

## Capability matrix

| Capability | Orchestrator | Worker | Observer |
|---|---|---|---|
| `state_read_global` | ✓ | ✓ | ✓ |
| `state_read_shared` | ✓ | ✓ | ✓ |
| `state_read_own` | ✓ | ✓ | ✓ |
| `state_write_global` | ✓ | — | — |
| `state_write_shared` | ✓ | ✓ | — |
| `state_write_own` | ✓ | ✓ | — |
| `agent_list` | ✓ | ✓ | ✓ |
| `agent_revoke` | ✓ | — | — |
| `token_verify` | ✓ | ✓ | ✓ |

---

## Token format

```
base64url(JSON claims, no padding) . hex(HMAC-SHA256(payload, secret))
```

Claims payload:
```json
{
  "agent_id": "uuid-v4",
  "role": "worker",
  "capabilities": ["state_read_global", "state_write_shared", "..."],
  "issued_at": "2025-01-01T00:00:00+00:00",
  "expires_at": "2025-01-01T01:00:00+00:00",
  "issuer": "robopotato"
}
```

The signature covers the entire base64 payload string. Any modification to the claims (role escalation, capability injection, expiry extension) invalidates the signature. Verification uses constant-time comparison to prevent timing attacks.

---

## Test suite

### Unit tests (no server required)

```bash
cargo test                        # in-memory store + token engine
cargo test --features persist     # with SQLite persistence
```

### Adversarial integration tests

Start the server first:
```bash
ROBOPOTATO_SECRET=test-secret cargo run
```

Then in a separate terminal:
```bash
cd tests
pip install httpx
python3 test_adversarial.py -v
```

**20 adversarial scenarios** covering:
- Expired token replay attacks
- Token forgery (wrong HMAC secret)
- Privilege escalation via payload tampering
- Cross-namespace pollution (worker → global/other-agent writes)
- Observer write attempts
- Mid-task agent revocation
- OCC storm (5 concurrent writers, exactly 1 wins)
- Missing / malformed authorization headers
- Public endpoint accessibility

### Claude Code comparison test

Requires `claude` CLI installed and configured:
```bash
cd tests
python3 compare.py
```

Runs the same multi-agent task with and without robopotato and prints a side-by-side security comparison table.

---

## Project structure

```
robopotato/
├── src/
│   ├── main.rs              # Server entry point, router, WebSocket handler
│   ├── config.rs            # Environment-based configuration
│   ├── errors.rs            # Typed AppError → HTTP response mapping
│   ├── auth/
│   │   ├── token.rs         # TokenEngine: sign, verify, TokenClaims
│   │   └── middleware.rs    # axum auth middleware (Bearer token + revocation)
│   ├── state/
│   │   ├── store.rs         # In-memory RwLock KV store with OCC
│   │   ├── namespace.rs     # Namespace parsing and capability resolution
│   │   └── persistence.rs   # SQLite write-through (--features persist)
│   ├── events/
│   │   └── bus.rs           # Tokio broadcast event bus
│   └── routes/
│       ├── agents.rs        # POST /agents/register, DELETE /agents/:id
│       ├── state.rs         # GET/PUT/DELETE /state/:key, GET /state/namespace/:ns
│       └── tokens.rs        # POST /tokens/verify
├── tests/
│   ├── robopotato_client.py # Python HTTP client wrapper
│   ├── test_adversarial.py  # 20 programmatic adversarial tests
│   ├── test_claude_code_agents.py  # Claude Code agent comparison harness
│   └── compare.py           # Side-by-side with/without runner
├── WHITEPAPER.md            # Research background and design rationale
├── CHANGELOG.md
├── CONTRIBUTING.md
└── Cargo.toml
```

---

## Why robopotato?

Multi-agent AI systems fail in three recurring ways that no amount of prompt-engineering reliably fixes:

- **Identity spoofing** — one agent claims to be another, inheriting unearned trust
- **Unauthorized state mutation** — a compromised or confused agent overwrites data it shouldn't touch
- **Silent concurrency conflicts** — two agents write the same key; the last write wins silently

robopotato addresses all three at the infrastructure layer with cryptographic enforcement, so agent correctness doesn't depend on model-level safety compliance.

See [WHITEPAPER.md](WHITEPAPER.md) for the full research context, threat model, and design rationale.

---

## License

MIT — see [LICENSE](LICENSE).
