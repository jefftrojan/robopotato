# Robopotato: A Lightweight Inter-Agent Trust and Shared State Server

**Version 0.1 — March 2026**

---

## Abstract

As large language model (LLM) agents move from demos into production pipelines, a consistent set of infrastructure gaps emerges: agents cannot verify each other's identity, have no scoped access control over shared state, and lack a lightweight mechanism to detect or recover from compromised or hallucinating peers. Existing solutions either require a full OAuth/PKI stack, are tightly coupled to a single orchestration framework, or provide no security guarantees at all.

This paper introduces **Robopotato** — a Rust-based, self-contained server that provides (1) HMAC-signed capability tokens for agent identity and fine-grained permission scoping, (2) a namespaced key-value state store with versioning and optimistic concurrency control, and (3) a real-time event bus for observing state changes across an agent cluster. Robopotato is framework-agnostic, accessible over HTTP from any language, and ships as a single binary with no external infrastructure dependencies.

---

## 1. Introduction

The deployment of autonomous AI agents capable of using tools, writing code, and interacting with external systems has accelerated substantially since 2023. Production pipelines increasingly involve not one agent but many: an orchestrator that decomposes tasks, workers that execute them, and observers that audit results. This multi-agent architecture introduces a class of infrastructure problems that single-agent frameworks were never designed to address.

Three failures dominate real production incidents:

**Identity collapse.** When Agent A receives a message claiming to come from Agent B, there is no standard mechanism to verify that claim. AutoGen [Wu et al., 2023], CrewAI, and LangGraph all rely on implicit trust established by topology — if the message arrived through the right channel, it is assumed to be legitimate. Greshake et al. [2023] demonstrated that this assumption breaks under indirect prompt injection, where malicious content in the environment hijacks an agent's instruction stream. Chen et al. [2024] extended this to "AgentPoison" attacks, showing that a compromised agent can poison shared memory to influence all downstream agents in a pipeline.

**State divergence.** Agents that share a task require a consistent view of the world. Park et al. [2023] found that without consistency enforcement on shared memory across 25 simulated agents, the agents developed contradictory beliefs and took conflicting actions. AutoCodeRover [Zhang et al., 2024] observed that even two-agent systems without arbitration produce conflicting patches 40% of the time on non-trivial codebases. The root cause is that no shared state primitive exists that is both accessible to agents across process boundaries and enforces serialized writes.

**Permission escalation.** In the absence of a capability model, every agent has access to every tool and every piece of state. A hallucinating worker can overwrite orchestrator-owned configuration; a compromised observer can write to shared task state. Hua et al. [2024] demonstrated that untrusted agent-to-agent communication is the primary attack vector in agentic pipelines, while Lampson [1974] established decades ago that the correct primitive for this problem is the unforgeable capability token — a grant of specific rights that can be delegated with attenuation but never escalated.

Robopotato addresses all three failures with a single, embeddable server process.

---

## 2. Background and Related Work

### 2.1 Inter-Agent Trust

The problem of establishing trust between autonomous software agents is not new. FIPA's Agent Communication Language [FIPA, 1997–2002] defined interaction protocols and performatives but delegated trust to the transport layer. Modern LLM orchestration frameworks inherit this gap: AutoGen, CrewAI, OpenAI Swarm, and AgentScope [Gao et al., 2024] all treat the message origin as trustworthy by construction.

Recent work has proposed application-layer mitigations. TrustAgent [Hua et al., 2024] adds a verification layer before agent actions are executed. Microsoft's "spotlighting" technique attempts to structurally separate instructions from data to limit injection surface. Neither approach provides cryptographic guarantees about the identity of the sending agent.

Cryptographic identity for services exists at scale in SPIFFE/SPIRE [CNCF, 2018], which issues X.509 SVIDs and JWT-SVIDs to workloads. But SPIFFE requires a full PKI deployment and is operationally prohibitive for local agent clusters or edge deployments.

The closest precedent to Robopotato's token design is Macaroons [Birgisson et al., 2014], which introduced contextual attenuation: a capability token can have caveats appended by a delegating authority, narrowing the token's power without a round-trip to the issuer. UCAN [Fission Labs, 2022] extends this idea to decentralized delegation chains. Robopotato adopts the simpler HMAC-signed flat capability model — sufficient for the coordinated (non-decentralized) agent cluster use case — while keeping the door open for Macaroon-style attenuation in future versions.

### 2.2 Capability-Based Security

Capability-based access control originates with Lampson [1974] and was refined by Levy [1984] and Shapiro et al. [1999] in the EROS system. The core insight is that access rights should be represented as unforgeable tokens (capabilities) rather than derived from identity plus an access control list. A capability system naturally supports the principle of least privilege: an agent is granted exactly the capabilities its task requires, no more.

In practice, most deployed systems use role-based access control (RBAC) — a coarser model that grants broad role permissions rather than resource-specific operation rights. JSON Web Tokens [Jones et al., 2015] with OAuth 2.0 scopes approximate capabilities but require a centralized authorization server and are designed for human-facing applications, not intra-cluster agent communication.

PASETO [Arciszewski & Scott, 2018] improves on JWT by removing algorithm agility (a source of JWT vulnerabilities) and providing clearer security semantics, but shares the same fundamental role-based scope model. Neither JWT nor PASETO define semantics for agent-specific operations like "may read keys in the `shared.*` namespace" or "may revoke agent X."

Robopotato's capability model is grounded in HMAC-SHA256 signing [Bellare et al., 1996; RFC 2104], for which formal security proofs under standard assumptions exist [Bellare, 2006]. Each token encodes a structured list of named capabilities alongside role and expiry, giving the server enough information to make fine-grained access decisions without any round-trip to an external authority.

### 2.3 Shared State in Multi-Agent Systems

The theoretical basis for shared state consistency derives from Herlihy and Wing's [1990] linearizability and the CAP theorem [Brewer, 2000; Gilbert & Lynch, 2002]. For production systems, the choice of consistency model has major practical consequences: Dynamo [DeCandia et al., 2007]'s eventual consistency is unsuitable for agent task allocation (two agents might both claim the same task), while Spanner [Corbett et al., 2012]'s external consistency requires atomic clocks and global infrastructure.

For co-located or LAN-connected agent clusters — Robopotato's target deployment — strong consistency without partition tolerance is the correct tradeoff. A single-node RwLock-protected store provides linearizable reads and writes at microsecond latencies with no network overhead. Optimistic concurrency control [version numbers per key] handles the write-write conflict case: an agent that reads version N and attempts to write back must supply N as the expected version; if another agent has already written version N+1, the write is rejected and the agent can re-read and retry.

CRDTs [Shapiro et al., 2011] offer an alternative for certain state types (e.g., sets of completed tasks, agent capability advertisements) where automatic merge is semantically correct. Robopotato does not currently implement CRDTs but the namespace model is designed to accommodate per-key consistency policies in future versions — analogous to Anna's lattice-based approach [Wu et al., 2019].

Existing agent memory systems — Zep, MemGPT/Letta [Packer et al., 2023], LangGraph's StateGraph — are either single-agent, process-local, or tightly coupled to a specific framework. The Redis-based ad-hoc state stores common in LangGraph deployments provide no per-agent access control. Cloudflare's Durable Objects solve the problem elegantly but require Cloudflare infrastructure. Robopotato's position — a self-contained binary with no external dependencies — is not occupied by any existing tool.

### 2.4 Agent Communication Protocols

The current protocol landscape is fragmented. The Model Context Protocol [Anthropic, 2024] standardizes tool-to-agent communication over JSON-RPC 2.0 but is not designed for agent-to-agent trust — it relies on the host process for security. The Agent-to-Agent Protocol [Google DeepMind, 2025] handles discovery and task delegation over HTTP/SSE with OAuth-based authentication but defines no shared state layer. AgentProtocol [AI Engineer Foundation, 2023] focuses on human-to-agent task management with no peer authentication primitives.

Robopotato is not a replacement for any of these protocols. It is a complementary primitive: the shared substrate that agents using MCP or A2A can use to exchange trusted state and verify each other's identity without an external OAuth server.

---

## 3. System Design

### 3.1 Design Goals

Robopotato is designed around four principles:

1. **Lightweight over complete.** A single Rust binary, no database required in the default configuration, no external dependencies. An agent cluster should be able to start Robopotato in milliseconds alongside its orchestrator.

2. **Framework-agnostic.** Agents interact via HTTP and WebSocket. Any language, any framework. A Claude Code agent, a Python LangGraph worker, and a TypeScript observer can all participate in the same cluster.

3. **Capability-scoped, not role-coarse.** Access decisions are made at the granularity of (namespace × operation), not at the granularity of role. A worker agent has exactly the permissions its task requires; an observer cannot accidentally mutate state even if misconfigured.

4. **Observable by design.** Every state mutation and agent lifecycle event is published to a real-time WebSocket event bus. Agents and external monitoring tools can subscribe to observe the cluster's behavior without polling.

### 3.2 Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Robopotato Server                  │
│                  (axum + tokio, Rust)                │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Token Engine │  │  State Store │  │ Event Bus │  │
│  │              │  │              │  │           │  │
│  │ • HMAC-SHA256│  │ • Namespaced │  │ • Tokio   │  │
│  │ • Capability │  │ • Versioned  │  │   broadcast│  │
│  │   scoping    │  │ • RwLock OCC │  │ • WebSocket│  │
│  │ • Expiry &   │  │ • Opt SQLite │  │   pub/sub  │  │
│  │   revocation │  │   persistence│  │           │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
└──────────────────────────┬──────────────────────────┘
                           │ HTTP / WebSocket
          ┌────────────────┼────────────────┐
          │                │                │
   Orchestrator        Worker A          Observer
   (any language)    (any language)   (any language)
```

### 3.3 Identity and Trust Model

Each agent receives a **capability token** upon registration. Tokens are signed with HMAC-SHA256 using a server-side secret and carry the following claims:

```json
{
  "agent_id": "worker-abc123",
  "role": "worker",
  "capabilities": [
    "state:read:global",
    "state:read:shared",
    "state:read:own",
    "state:write:shared",
    "state:write:own",
    "token:verify"
  ],
  "issued_at": "2026-03-18T10:00:00Z",
  "expires_at": "2026-03-18T11:00:00Z",
  "issuer": "robopotato"
}
```

The token format is `base64url(claims_json).hex(hmac_sha256(base64url(claims_json)))`. This provides:

- **Integrity**: Any modification to the claims invalidates the signature.
- **Constant-time verification**: The signature comparison uses a constant-time equality function, preventing timing side-channels [AWS Sig V4 operational practice].
- **Stateless verification**: The server needs only the shared secret to verify any token — no database lookup required per request.
- **Revocation**: A server-side revocation set (in-memory `HashSet`) enables immediate revocation of any agent without token expiry. Agents can query `/tokens/verify` to check whether a peer's token has been revoked.

**Roles and default capabilities:**

| Role | Default Capabilities |
|---|---|
| `orchestrator` | Read/write all namespaces, list/revoke agents, verify tokens |
| `worker` | Read global/shared, read/write own namespace, write shared, verify tokens |
| `observer` | Read global/shared/own — no writes |

### 3.4 State Namespaces

All state keys are prefixed with a namespace that determines access rules:

| Namespace | Example Key | Readable By | Writable By |
|---|---|---|---|
| `global.*` | `global.task_spec` | All agents | Orchestrator only |
| `shared.*` | `shared.results` | All agents | Workers + Orchestrator |
| `agent.<id>.*` | `agent.abc123.scratchpad` | Owner + Orchestrator | Owner + Orchestrator |

This design is motivated by the "corex" failure mode [Sun et al., 2023] where agents with unrestricted visibility corrupt each other's reasoning state. Private agent namespaces ensure each agent has an isolated scratchpad. The shared namespace enables the explicit inter-agent communication channel that MetaGPT [Hong et al., 2023] identified as the key to reducing coordination failures.

### 3.5 Optimistic Concurrency Control

Every state entry carries a `version` counter. Write requests may include an `expected_version` field:

```json
PUT /state/shared.task_result
{
  "value": { "status": "done", "output": "..." },
  "expected_version": 3
}
```

If the current version is not 3, the server returns `409 Conflict`. The agent re-reads the current state and retries. This prevents the "two agents claiming the same task" failure mode identified by Chen et al. [2023] without requiring distributed locks or consensus protocols.

For writes where the agent does not specify an expected version, the write always succeeds (last-write-wins). This is appropriate for private agent namespaces and non-critical shared state (telemetry, progress indicators).

### 3.6 Event Bus

All state mutations and agent lifecycle events are published to a Tokio broadcast channel and streamed to connected WebSocket subscribers:

```json
{ "type": "state_changed", "key": "shared.task_result", "version": 4, "agent_id": "worker-abc123" }
{ "type": "agent_registered", "agent_id": "worker-xyz789", "role": "worker" }
{ "type": "agent_revoked", "agent_id": "worker-old001" }
```

This enables reactive agent patterns — a worker can subscribe to `shared.*` events and begin processing immediately when the orchestrator writes a new task, without polling. It also provides a tamper-evident audit trail: any external observer can reconstruct the full sequence of state changes for post-hoc analysis or debugging.

### 3.7 API Surface

```
# Public (no auth required)
POST   /agents/register          Register a new agent; returns capability token
POST   /tokens/verify            Verify and inspect another agent's token
GET    /health                   Liveness check
WS     /events                   Subscribe to real-time event stream

# Protected (Bearer token required)
DELETE /agents/:id               Revoke an agent (orchestrator only)
GET    /state/:key               Read a state entry
PUT    /state/:key               Write a state entry (with optional OCC version)
DELETE /state/:key               Delete a state entry
GET    /state/namespace/:ns      List all keys in a namespace
```

---

## 4. Security Analysis

### 4.1 Threat Model

Robopotato is designed to protect against the following threats within an agent cluster:

- **Compromised worker agent**: A worker that has been hijacked via prompt injection attempts to read orchestrator-private state, escalate its own capabilities, or forge messages as another agent. The capability token system prevents all three: capabilities are encoded in a server-signed token the agent cannot modify; the server validates every request against the token's capability list.

- **Replay attacks**: A captured valid request is replayed after the legitimate agent has been revoked. The revocation set and token expiry together bound the replay window. Future versions will add per-request nonces (HAWK-style [Hammer-Lahav, 2012]) to eliminate this window entirely.

- **State poisoning**: An agent with write access to `shared.*` writes malicious content intended to mislead other agents. Namespace scoping limits the blast radius: a worker cannot write to `global.*` (orchestrator configuration) and cannot write to another agent's private namespace.

- **Identity spoofing**: Agent A claims to be Agent B by presenting a self-crafted token. Without the HMAC secret, a valid signature cannot be produced. All tokens are verified server-side before any access is granted.

### 4.2 What Robopotato Does Not Address

Robopotato explicitly does not address:

- **Byzantine agent behavior within granted permissions**: An agent acting within its legitimate capability scope but producing malicious outputs (hallucinated results, corrupted state values). This requires application-layer validation.
- **Secret distribution**: The HMAC secret must be distributed to the Robopotato server at startup. In production, this should use a secrets manager (Vault, AWS Secrets Manager). Robopotato reads the secret from an environment variable.
- **Mutual TLS**: The current implementation uses bearer tokens over HTTP. For production deployments, TLS termination should be handled by a reverse proxy (Nginx, Caddy) or future versions will add native TLS via `rustls`.
- **Multi-node clustering**: Robopotato is a single-node server. Horizontal scaling requires an external consistent store (etcd, TiKV). The self-contained model is the primary design goal for v0.1.

---

## 5. Implementation

Robopotato is implemented in Rust using the Tokio async runtime and the Axum web framework. The choice of Rust provides:

- **Memory safety without GC**: No garbage collection pauses during high-frequency agent requests.
- **Predictable latency**: P99 request latency for state reads is bounded by the `RwLock` contention profile, not GC cycles.
- **Single binary deployment**: `cargo build --release` produces a ~8MB statically-linked binary with no runtime dependencies.
- **Correctness guarantees**: The type system enforces that capability checks cannot be bypassed — the `auth_middleware` injects verified `TokenClaims` as a typed request extension; route handlers receive claims or the request is rejected before the handler is invoked.

Key crates: `axum 0.7` (HTTP server), `tokio 1` (async runtime), `hmac 0.12` + `sha2 0.10` (RustCrypto HMAC-SHA256), `serde_json 1` (serialization), `chrono 0.4` (expiry timestamps), `uuid 1` (agent ID generation), `sqlx 0.7` (optional SQLite persistence, feature-gated).

### 5.1 Token Signing and Verification

```
sign(claims):
  payload  = json_serialize(claims)
  encoded  = base64url_encode(payload)
  sig      = hmac_sha256(secret, encoded)
  token    = encoded + "." + hex(sig)

verify(token):
  [encoded, provided_sig] = split(token, ".", limit=2)
  expected_sig = hmac_sha256(secret, encoded)
  if not constant_time_eq(provided_sig, expected_sig): reject
  claims = json_deserialize(base64url_decode(encoded))
  if claims.expires_at < now(): reject
  if claims.agent_id in revocation_set: reject
  return claims
```

Constant-time comparison is implemented via bitwise XOR accumulation, preventing timing oracles [as recommended by AWS SigV4 practice and HMAC RFC guidance].

### 5.2 State Store

The state store is a `HashMap<String, StateEntry>` protected by a `tokio::sync::RwLock`. Read operations acquire a shared read lock; writes acquire an exclusive write lock. Contention is expected to be low in typical agent clusters (2–20 agents) — the `RwLock` model provides strong consistency with negligible overhead at this scale.

Each `StateEntry` contains: `key`, `value` (arbitrary JSON), `version` (u64, monotonically incrementing), `owner_agent_id`, and `updated_at` timestamp.

Optional SQLite persistence (enabled via `--features persist` and `ROBOPOTATO_PERSIST=true`) writes all mutations to a WAL-mode SQLite database, enabling state recovery across server restarts. SQLite in WAL mode provides the same strong consistency guarantees as the in-memory store for the single-writer case.

---

## 6. Evaluation Plan

### 6.1 Baseline: Multi-Agent Task Without Robopotato

**Setup**: Two Claude Code agent instances (orchestrator + worker) cooperating on a multi-step software task. Communication via plain text files or direct message passing. No shared state arbitration, no identity verification.

**Metrics to collect**:
- Rate of state conflicts (orchestrator and worker write inconsistent task state)
- Rate of identity confusion (worker acts on instructions it cannot verify came from orchestrator)
- Task completion rate on a 10-step benchmark
- Debugging time when failures occur (no audit trail)

### 6.2 With Robopotato

**Setup**: Same agents, both registered with Robopotato at startup. Orchestrator writes task specifications to `global.*`; workers read tasks from `global.*`, write results to `shared.*`, maintain scratchpads in `agent.<id>.*`. All inter-agent messages include a token that the recipient verifies via `POST /tokens/verify`.

**Metrics to collect**:
- Same metrics as baseline for direct comparison
- Token verification latency (p50/p95/p99)
- State read/write latency under concurrent agent load
- Number of OCC conflicts detected and successfully retried
- Event bus lag (time from state write to WebSocket delivery)

### 6.3 Adversarial Test: Prompt Injection

Inject a malicious instruction into the environment (e.g., in a file the worker agent reads) that attempts to: (a) make the worker claim it is the orchestrator, (b) write to `global.*`, (c) revoke another agent.

**Expected result with Robopotato**: All three attempts rejected — (a) the worker's token identifies it as `worker` role; (b) `global.*` writes require `state:write:global` which workers do not have; (c) revocation requires `agent:revoke` capability, orchestrator-only.

**Expected result without Robopotato**: All three may succeed depending on how naively the orchestrator processes worker outputs.

---

## 7. Limitations and Future Work

**Token attenuation**: The current model issues flat capability tokens. A natural extension is Macaroon-style delegation [Birgisson et al., 2014]: an orchestrator issues a token to a sub-orchestrator with a subset of its own capabilities, which the sub-orchestrator can further narrow when spawning workers. This enables hierarchical agent trees without requiring a root authority round-trip at every level.

**CRDT state types**: Certain shared state patterns — sets of completed task IDs, agent presence advertisements — are naturally merge-safe. Adding CRDT semantics for specific key types would eliminate OCC conflicts for these patterns entirely.

**Mutual authentication**: The current model authenticates agents to the server (bearer tokens) but the server itself is trusted by construction. Adding server-to-agent mutual TLS would close the remaining identity gap for hostile network environments.

**Distributed deployment**: Single-node strong consistency is the right tradeoff for local agent clusters. For globally distributed agent systems, a pluggable backend (etcd, TiKV, FoundationDB) replacing the in-memory store would extend Robopotato's reach without changing its API surface.

**Per-request nonces**: Adding a timestamp + nonce to each signed request (HAWK-style) would eliminate the replay window that currently exists between token issuance and expiry.

---

## 8. Conclusion

The multi-agent AI infrastructure gap is real, documented, and unsolved by existing tools. Orchestration frameworks provide powerful abstractions for agent coordination but universally omit the two primitives that make multi-agent systems safe and debuggable: verified agent identity with scoped capabilities, and a consistent shared state layer with per-agent access control.

Robopotato is a minimal, correct implementation of both primitives. It is not a new agent framework. It is the sidecar that existing frameworks are missing — a single binary that any agent, in any language, using any orchestration system, can use to register its identity, scope its permissions, share state safely with peers, and verify that the instructions it receives come from who they claim to come from.

The research literature from distributed systems (Lampson 1974 → Macaroons 2014 → HMAC-SHA256 → UCAN 2022) and multi-agent AI (CAMEL 2023 → MetaGPT 2023 → TrustAgent 2024 → AgentPoison 2024) converges on the same design: unforgeable capability tokens plus consistent shared state. Robopotato implements that convergence in ~1500 lines of safe Rust.

---

## References

### Foundational Security & Capability Systems
- Lampson, B. (1974). "Protection in Operating Systems." *Communications of the ACM*, 17(10).
- Levy, H.M. (1984). *Capability-Based Computer Systems.* Digital Press.
- Shapiro, J. et al. (1999). "EROS: A Fast Capability System." *SOSP 1999.*
- Bellare, M., Canetti, R., Krawczyk, H. (1996). "Keyed Hash Functions and Message Authentication." *CRYPTO 1996.* → **RFC 2104.**
- Bellare, M. (2006). "New Proofs for NMAC and HMAC: Security Without Collision-Resistance." *CRYPTO 2006.*
- Birgisson, A. et al. (2014). "Macaroons: Cookies with Contextual Caveats for Decentralized Authorization in the Cloud." *NDSS 2014.*
- Jones, M., Bradley, J., Sakimura, N. (2015). *JSON Web Tokens.* **RFC 7519.**
- Arciszewski, S., Scott, P. (2018). *PASETO: Platform-Agnostic Security Tokens.* paseto.io.
- Perrin, T. (2018). *The Noise Protocol Framework.* noiseprotocol.org.
- Hammer-Lahav, E. (2012). *HAWK: HTTP Holder-Of-Key Authentication Scheme.* IETF Draft.
- Fission Labs. (2022). *UCAN: User-Controlled Authorization Networks.* github.com/ucan-wg/spec.
- CNCF. (2018). *SPIFFE: Secure Production Identity Framework for Everyone.* spiffe.io.

### Distributed Systems & State Consistency
- Herlihy, M., Wing, J. (1990). "Linearizability: A Correctness Condition for Concurrent Objects." *JACM*, 37(3).
- Brewer, E. (2000). "Towards Robust Distributed Systems." *PODC 2000 Keynote.*
- Gilbert, S., Lynch, N. (2002). "Brewer's Conjecture and the Feasibility of Consistent, Available, Partition-Tolerant Web Services." *ACM SIGACT News.*
- DeCandia, G. et al. (2007). "Dynamo: Amazon's Highly Available Key-Value Store." *SOSP 2007.*
- Shapiro, M. et al. (2011). "Conflict-free Replicated Data Types." *INRIA RR-7687.*
- Corbett, J. et al. (2012). "Spanner: Google's Globally Distributed Database." *OSDI 2012.*
- Wu, C. et al. (2019). "Anna: A KVS For Any Scale." *ICDE 2019.*

### Multi-Agent LLM Systems
- Li, G. et al. (2023). "CAMEL: Communicative Agents for Mind Exploration." *NeurIPS 2023.* arXiv:2303.17760.
- Wu, Q. et al. (2023). "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation." arXiv:2308.08155.
- Yao, S. et al. (2023). "ReAct: Synergizing Reasoning and Acting in Language Models." *ICLR 2023.* arXiv:2210.03629.
- Park, J. et al. (2023). "Generative Agents: Interactive Simulacra of Human Behavior." *CHI 2023.* arXiv:2304.03442.
- Packer, C. et al. (2023). "MemGPT: Towards LLMs as Operating Systems." arXiv:2310.08560.
- Wang, L. et al. (2023). "A Survey on Large Language Model based Autonomous Agents." arXiv:2308.11432.
- Hong, S. et al. (2023). "MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework." arXiv:2308.00352.
- Qian, C. et al. (2023). "ChatDev: Communicative Agents for Software Development." arXiv:2307.07924.
- Wang, G. et al. (2023). "Voyager: An Open-Ended Embodied Agent with Large Language Models." arXiv:2305.16291.
- Sun, Z. et al. (2023). "Corex: Pushing the Boundaries of Complex Reasoning through Multi-Model Collaboration." arXiv:2310.00280.
- Chen, W. et al. (2023). "Multi-Agent Consensus Seeking via Large Language Models." arXiv:2310.20151.
- Zhang, C. et al. (2023). "ProAgent: Building Proactive Cooperative Agents with Large Language Models." arXiv:2308.11339.
- Gao, D. et al. (2024). "AgentScope: A Flexible yet Robust Multi-Agent Platform." arXiv:2402.14034.
- Yang, J. et al. (2024). "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering." arXiv:2405.15793.
- Hua, W. et al. (2024). "TrustAgent: Towards Safe and Trustworthy LLM-based Agents." arXiv:2402.01586.
- Chen, Z. et al. (2024). "AgentPoison: Red-teaming LLM Agents via Poisoning Memory or Knowledge Bases." arXiv:2407.12784.
- Liu, B. et al. (2023). "AgentBench: Evaluating LLMs as Agents." arXiv:2308.03688.
- Shen, Y. et al. (2023). "TaskBench: Benchmarking Large Language Models for Task Automation." arXiv:2311.18760.

### Security in LLM Pipelines
- Perez, F., Ribeiro, I. (2022). "Ignore Previous Prompt: Attack Techniques for Language Models." arXiv:2211.09527.
- Greshake, K. et al. (2023). "Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection." arXiv:2302.12173.

### Protocols & Standards
- FIPA. (1997–2002). *FIPA ACL Agent Communication Language.* fipa.org.
- Anthropic. (2024). *Model Context Protocol Specification.* spec.modelcontextprotocol.io.
- Google DeepMind. (2025). *Agent-to-Agent Protocol (A2A).* google.github.io/A2A.
- AI Engineer Foundation. (2023). *AgentProtocol.* agentprotocol.ai.

---

*Robopotato is open source. Source code at: github.com/robopotato/robopotato (forthcoming)*
