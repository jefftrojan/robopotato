<div align="center">

<img src="assets/robopotato_logo.png" alt="robopotato" width="160" />

# robopotato

**The missing trust layer for AI agent swarms.**

[![CI](https://github.com/jefftrojan/robopotato/actions/workflows/ci.yml/badge.svg)](https://github.com/jefftrojan/robopotato/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Rust](https://img.shields.io/badge/rust-1.75%2B-orange.svg)](https://www.rust-lang.org)
[![crates.io](https://img.shields.io/crates/v/robopotato.svg)](https://crates.io/crates/robopotato)

*Cryptographic identity · namespaced shared state · optimistic concurrency control*

[Quick start](#quick-start) · [API reference](#api-reference) · [Demo](#live-demo) · [Whitepaper](WHITEPAPER.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## The problem

The moment you run more than one AI agent in the same environment, three things go wrong:

| Failure mode | What happens without robopotato |
|---|---|
| **Identity spoofing** | Agent B claims to be Agent A. There is no cryptographic check. Trust is a prompt. |
| **Unauthorized mutation** | Any agent can overwrite any shared state. A confused worker corrupts the orchestrator's config silently. |
| **Silent concurrency conflicts** | Two agents read version 3, both write version 4. Last writer wins. The first write disappears. Nobody notices. |

robopotato fixes all three at the infrastructure layer — not the prompt layer.

---

## Live demo

Start the server, then run:

```bash
ROBOPOTATO_SECRET=test-secret cargo run &
./demo.sh
```

```
▶ Registering agents
  ✓ Orchestrator registered  (id: a1b2c3d4...)
  ✓ Worker registered         (id: e5f6a7b8...)
  ✓ Observer registered       (id: c9d0e1f2...)
  ✓ Rogue worker registered   (id: 33445566...)

▶ Namespace isolation — workers cannot write global.*
  ✓ Orchestrator writes global.config → HTTP 200
  ✗ BLOCKED — Worker PUT global.config → HTTP 403 (missing capability: state_write_global)

▶ Observer is read-only — cannot write shared.*
  ✗ BLOCKED — Observer PUT shared.results → HTTP 403 (missing capability: state_write_shared)

▶ Agent namespace isolation — workers cannot write each other's state
  ✗ BLOCKED — Worker PUT agent.<orchestrator>.private → HTTP 403 (not the owner)
  ✓ Worker PUT agent.<self>.private → HTTP 200 (owner allowed)

▶ Optimistic Concurrency Control — concurrent writers, one wins
  ✓ Worker A writes shared.task (expected_version=1) → HTTP 200
  ✗ BLOCKED — Worker B writes shared.task (expected_version=1) → HTTP 409 (version conflict: stale read detected)

▶ Mid-task revocation — rogue agent blocked after orchestrator revokes
  ✓ Rogue agent writes before revocation → HTTP 200
  ✓ Orchestrator revokes rogue agent → HTTP 200
  ✗ BLOCKED — Rogue agent write after revocation → HTTP 401 (agent has been revoked)

▶ Token forgery — wrong HMAC secret is rejected
  ✗ BLOCKED — Forged token PUT global.pwned → HTTP 401 (invalid token signature)

  Summary

  ✓  Namespace isolation    workers cannot touch global.* or other agents' namespaces
  ✓  Role enforcement       observers are permanently read-only
  ✓  OCC conflict detection concurrent stale writes produce 409, not silent overwrites
  ✓  Mid-task revocation    revoked tokens rejected immediately, no restart needed
  ✓  Token forgery rejected wrong HMAC secret → 401 every time

  All of the above enforced at the infrastructure layer.
  No prompt-engineering required.
```

---

## Quick start

```bash
git clone https://github.com/jefftrojan/robopotato
cd robopotato
ROBOPOTATO_SECRET=change-me cargo run --release
```

Server starts on `http://127.0.0.1:7878`. Register an agent and start calling:

```bash
# Register
TOKEN=$(curl -sf -X POST http://127.0.0.1:7878/agents/register \
  -H "Content-Type: application/json" \
  -d '{"role":"worker"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Write shared state
curl -X PUT http://127.0.0.1:7878/state/shared.task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": {"status": "pending"}}'

# Read it back
curl http://127.0.0.1:7878/state/shared.task \
  -H "Authorization: Bearer $TOKEN"
```

With SQLite persistence across restarts:

```bash
ROBOPOTATO_SECRET=change-me \
ROBOPOTATO_PERSIST=true \
ROBOPOTATO_DB_PATH=./robopotato.db \
cargo run --release --features persist
```

---

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│                        robopotato                           │
│                                                             │
│  POST /agents/register  →  HMAC-SHA256 capability token     │
│                                                             │
│  PUT  /state/{key}      →  verify token                     │
│                            check capability for namespace   │
│                            check expected_version (OCC)     │
│                            write + publish event            │
│                                                             │
│  GET  /events (WS)      →  live stream of all changes       │
└─────────────────────────────────────────────────────────────┘
       ↑                          ↑                    ↑
  orchestrator               worker(s)            observer(s)
```

Tokens are `base64url(claims_json).hex(hmac_sha256)` — no JWT library, no asymmetric key management, no external auth service. Drop robopotato next to your agents and point them at it.

---

## Features

| | |
|---|---|
| **HMAC-SHA256 tokens** | Signed capability tokens. Constant-time verification. No JWT dependency. |
| **Three roles** | `orchestrator` · `worker` · `observer` |
| **Nine capabilities** | `state_read/write_{global,shared,own}` · `agent_list` · `agent_revoke` · `token_verify` |
| **Namespaced KV store** | `global.*` · `shared.*` · `agent.<id>.*` with per-namespace write rules |
| **OCC** | `expected_version` on writes → 409 Conflict on stale reads instead of silent overwrites |
| **Agent revocation** | Orchestrators revoke agents mid-flight; takes effect on the next request |
| **WebSocket event bus** | Live stream of `StateChanged`, `StateDeleted`, `AgentRegistered`, `AgentRevoked` |
| **SQLite persistence** | `--features persist` — WAL mode, write-through, startup recovery |
| **Framework-agnostic** | Plain JSON over HTTP — Python, Node, Go, curl, anything |
| **Self-contained** | Single Rust binary, no external infrastructure |

---

## Capability matrix

| Capability | Orchestrator | Worker | Observer |
|---|:---:|:---:|:---:|
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

## API reference

### Public endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `POST` | `/agents/register` | Register agent, receive signed token |
| `POST` | `/tokens/verify` | Verify token validity + revocation status |
| `GET` | `/events` | WebSocket — live event stream |

### Protected endpoints (Bearer token required)

| Method | Path | Description |
|---|---|---|
| `GET` | `/state/{key}` | Read a state entry |
| `PUT` | `/state/{key}` | Write a state entry (optional OCC via `expected_version`) |
| `DELETE` | `/state/{key}` | Delete a state entry |
| `GET` | `/state/namespace/{ns}` | List all keys under a namespace prefix |
| `DELETE` | `/agents/{id}` | Revoke an agent (orchestrator only) |

**OCC write example:**
```jsonc
// PUT /state/shared.task
{
  "value": { "status": "done" },
  "expected_version": 3   // omit for last-writer-wins; 409 if current version ≠ 3
}
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ROBOPOTATO_SECRET` | **required** | HMAC signing secret |
| `ROBOPOTATO_HOST` | `127.0.0.1` | Bind address |
| `ROBOPOTATO_PORT` | `7878` | Bind port |
| `ROBOPOTATO_TOKEN_TTL` | `3600` | Token lifetime (seconds) |
| `ROBOPOTATO_PERSIST` | `false` | Enable SQLite persistence |
| `ROBOPOTATO_DB_PATH` | `robopotato.db` | SQLite file path |

---

## Testing

```bash
# Unit tests (no server)
cargo test
cargo test --features persist

# Adversarial integration tests (20 scenarios)
ROBOPOTATO_SECRET=test-secret cargo run &
cd tests && python3 test_adversarial.py -v

# With/without comparison using Claude Code agents
cd tests && python3 compare.py
```

Adversarial scenarios include: expired token replay, wrong-secret forgery, payload tampering, cross-namespace pollution, observer write attempts, mid-task revocation, OCC storm (5 concurrent writers), and malformed token formats.

---

## Project structure

```
src/
├── main.rs                  # Router, WebSocket handler
├── auth/
│   ├── token.rs             # TokenEngine: sign, verify, claims
│   └── middleware.rs        # Bearer token enforcement
├── state/
│   ├── store.rs             # RwLock KV store with OCC
│   ├── namespace.rs         # Namespace parsing + capability mapping
│   └── persistence.rs       # SQLite write-through (--features persist)
├── events/bus.rs            # Tokio broadcast event bus
└── routes/                  # agents, state, tokens handlers
tests/
├── test_adversarial.py      # 20 programmatic adversarial tests
├── robopotato_client.py     # Python HTTP client
└── compare.py               # With/without Claude Code comparison
```

---

## Why not X?

| | robopotato | Redis | a shared database | prompt instructions |
|---|---|---|---|---|
| Cryptographic agent identity | ✓ | — | — | — |
| Per-namespace capability enforcement | ✓ | — | — | — |
| OCC with version conflicts | ✓ | partial | partial | — |
| Agent revocation | ✓ | — | — | — |
| Live event bus | ✓ | ✓ | — | — |
| Self-contained single binary | ✓ | — | — | ✓ |
| Works with any AI framework | ✓ | ✓ | ✓ | ✓ |

robopotato is not a general-purpose database. It is a trust and coordination primitive specifically shaped for AI agent workloads.

---

## Whitepaper

[WHITEPAPER.md](WHITEPAPER.md) covers the threat model, research context, cryptographic design, and evaluation methodology with 40+ citations.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues are labelled [`good first issue`](https://github.com/jefftrojan/robopotato/issues?q=is%3Aopen+label%3A%22good+first+issue%22).

## License

MIT — see [LICENSE](LICENSE).
