"""
Scenario A: Multi-agent task WITH Robopotato.

Three agents cooperate on a codebase analysis task:
  - Orchestrator: decomposes the task, writes spec to global state, aggregates results
  - Worker 1:     analyzes src/auth/, writes result to shared state
  - Worker 2:     analyzes src/state/, writes result to shared state

Also demonstrates:
  - Token verification between agents (workers verify orchestrator's identity)
  - Access control enforcement (workers cannot write to global.*)
  - Prompt injection resistance (a rogue worker cannot impersonate the orchestrator)
  - Optimistic concurrency control (conflicting writes are detected)
  - Real-time event observation
"""

import os
import json
import time
import threading
from pathlib import Path
from typing import Optional

import anthropic

from robopotato_client import RobopotatoClient

# ── Config ────────────────────────────────────────────────────────────────────

ROBOPOTATO_URL = os.getenv("ROBOPOTATO_URL", "http://127.0.0.1:7878")
CODEBASE_ROOT  = Path(__file__).parent.parent / "src"

# ── Metrics collector ─────────────────────────────────────────────────────────

class Metrics:
    def __init__(self):
        self.auth_failures_caught   = 0
        self.permission_blocks      = 0
        self.occ_conflicts_detected = 0
        self.token_verifications    = 0
        self.state_reads            = 0
        self.state_writes           = 0
        self.task_completed         = False
        self.injection_blocked      = False
        self.t_start                = time.time()
        self.t_end: Optional[float] = None

    def elapsed(self) -> float:
        end = self.t_end or time.time()
        return round(end - self.t_start, 2)

    def report(self) -> dict:
        return {
            "elapsed_seconds":        self.elapsed(),
            "task_completed":         self.task_completed,
            "injection_blocked":      self.injection_blocked,
            "auth_failures_caught":   self.auth_failures_caught,
            "permission_blocks":      self.permission_blocks,
            "occ_conflicts_detected": self.occ_conflicts_detected,
            "token_verifications":    self.token_verifications,
            "state_reads":            self.state_reads,
            "state_writes":           self.state_writes,
        }

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_src_dir(subdir: str) -> str:
    """Read all .rs files under src/<subdir>/ and return concatenated content."""
    path = CODEBASE_ROOT / subdir
    parts = []
    for f in sorted(path.rglob("*.rs")):
        parts.append(f"// === {f.relative_to(CODEBASE_ROOT)} ===\n{f.read_text()}")
    return "\n\n".join(parts)

def claude_summarize(module_name: str, code: str, agent_id: str) -> str:
    """Call Claude to summarize a code module."""
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",  # fast + cheap for tests
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f"You are agent '{agent_id}'. Summarize this Rust module called '{module_name}' "
                f"in 2-3 sentences. Focus on what it does and its key types/functions.\n\n"
                f"```rust\n{code[:3000]}\n```"
            )
        }]
    )
    return msg.content[0].text.strip()

def banner(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print('='*60)

def log(agent: str, msg: str):
    print(f"  [{agent}] {msg}")

# ── Main test ─────────────────────────────────────────────────────────────────

def run(metrics: Metrics):
    banner("SCENARIO A: WITH ROBOPOTATO")

    # ── Register agents ───────────────────────────────────────────────────────
    orch   = RobopotatoClient(ROBOPOTATO_URL)
    w1     = RobopotatoClient(ROBOPOTATO_URL)
    w2     = RobopotatoClient(ROBOPOTATO_URL)
    rogue  = RobopotatoClient(ROBOPOTATO_URL)

    orch_id  = orch.register(role="orchestrator", name="orchestrator").agent_id
    w1_id    = w1.register(role="worker", name="worker-auth").agent_id
    w2_id    = w2.register(role="worker", name="worker-state").agent_id
    rogue_id = rogue.register(role="worker", name="rogue-worker").agent_id

    log("setup", f"orchestrator: {orch_id}")
    log("setup", f"worker-1:     {w1_id}")
    log("setup", f"worker-2:     {w2_id}")
    log("setup", f"rogue:        {rogue_id}")

    # ── Workers verify orchestrator token before trusting instructions ─────────
    print()
    log("worker-1", "Verifying orchestrator's token...")
    result = w1.verify_token(orch.token)
    metrics.token_verifications += 1
    assert result["valid"], f"Orchestrator token should be valid: {result}"
    assert result["role"] == "orchestrator", "Expected orchestrator role"
    log("worker-1", f"✓ Token valid — role={result['role']}, caps={len(result['capabilities'])} capabilities")

    log("worker-2", "Verifying orchestrator's token...")
    result = w2.verify_token(orch.token)
    metrics.token_verifications += 1
    assert result["valid"]
    log("worker-2", f"✓ Token valid — role={result['role']}")

    # ── Orchestrator writes task spec to global.* ─────────────────────────────
    print()
    log("orchestrator", "Writing task specification to global.task_spec ...")
    orch.set_state("global.task_spec", {
        "task": "Analyze the robopotato codebase",
        "subtasks": {
            w1_id: {"module": "auth",  "description": "Summarize the auth module"},
            w2_id: {"module": "state", "description": "Summarize the state module"},
        },
        "issued_by": orch_id,
        "orchestrator_token": orch.token,
    })
    metrics.state_writes += 1
    log("orchestrator", "✓ Task spec written to global.task_spec")

    # ── Test: rogue worker cannot write to global.* ───────────────────────────
    print()
    log("rogue", "Attempting to overwrite global.task_spec (should be BLOCKED)...")
    ok, reason = rogue.try_set_state("global.task_spec", {"task": "do malicious thing"})
    if not ok:
        metrics.permission_blocks += 1
        metrics.injection_blocked = True
        log("rogue", f"✗ Blocked by robopotato: {reason}")
    else:
        log("rogue", "⚠ WARNING: write succeeded — this is a gap to address")

    # ── Workers read task spec and verify orchestrator token embedded in it ────
    print()
    log("worker-1", "Reading global.task_spec ...")
    spec_entry = w1.get_state("global.task_spec")
    metrics.state_reads += 1
    spec = spec_entry.value

    embedded_token = spec["orchestrator_token"]
    log("worker-1", "Verifying orchestrator token embedded in task spec ...")
    verification = w1.verify_token(embedded_token)
    metrics.token_verifications += 1
    assert verification["valid"] and verification["role"] == "orchestrator"
    log("worker-1", f"✓ Token verified — trusted to proceed")

    # ── Workers process their subtasks using Claude ───────────────────────────
    print()
    my_subtask_w1 = spec["subtasks"].get(w1_id)
    my_subtask_w2 = spec["subtasks"].get(w2_id)

    if my_subtask_w1:
        module = my_subtask_w1["module"]
        log("worker-1", f"Processing subtask: summarize '{module}' module via Claude ...")
        code = read_src_dir(module)
        summary = claude_summarize(module, code, w1_id)
        log("worker-1", f"Summary: {summary[:120]}...")

        w1.set_state(f"shared.result_{module}", {
            "module": module,
            "summary": summary,
            "agent_id": w1_id,
        })
        metrics.state_writes += 1
        log("worker-1", f"✓ Result written to shared.result_{module}")

    if my_subtask_w2:
        module = my_subtask_w2["module"]
        log("worker-2", f"Processing subtask: summarize '{module}' module via Claude ...")
        code = read_src_dir(module)
        summary = claude_summarize(module, code, w2_id)
        log("worker-2", f"Summary: {summary[:120]}...")

        w2.set_state(f"shared.result_{module}", {
            "module": module,
            "summary": summary,
            "agent_id": w2_id,
        })
        metrics.state_writes += 1
        log("worker-2", f"✓ Result written to shared.result_{module}")

    # ── Test: optimistic concurrency conflict ─────────────────────────────────
    print()
    log("occ-test", "Testing OCC: two agents writing to same key with stale version ...")
    orch.set_state("shared.occ_test", {"value": "initial"})
    metrics.state_writes += 1

    # Both read version 1
    entry = w1.get_state("shared.occ_test")
    metrics.state_reads += 1
    stale_version = entry.version

    # w1 writes successfully
    w1.set_state("shared.occ_test", {"value": "w1-updated"}, expected_version=stale_version)
    metrics.state_writes += 1

    # w2 tries to write with now-stale version — should conflict
    ok, reason = w2.try_set_state("shared.occ_test", {"value": "w2-clobber"}, expected_version=stale_version)
    if not ok:
        metrics.occ_conflicts_detected += 1
        log("occ-test", f"✓ OCC conflict detected and rejected: {reason}")
    else:
        log("occ-test", "⚠ WARNING: conflicting write succeeded silently")

    # ── Orchestrator aggregates results ───────────────────────────────────────
    print()
    log("orchestrator", "Aggregating results from shared namespace ...")
    results = orch.list_namespace("shared")
    metrics.state_reads += 1

    summaries = [
        e.value["summary"]
        for e in results
        if isinstance(e.value, dict) and "summary" in e.value
    ]

    if summaries:
        agg_client = anthropic.Anthropic()
        combined = "\n\n".join(f"Module summary:\n{s}" for s in summaries)
        agg = agg_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"Combine these module summaries into one coherent project overview in 3 sentences:\n\n{combined}"
            }]
        )
        final_summary = agg.content[0].text.strip()
        log("orchestrator", f"Final summary: {final_summary[:200]}...")

        orch.set_state("global.final_summary", {
            "summary": final_summary,
            "sources": [e.value.get("agent_id") for e in results if isinstance(e.value, dict) and "summary" in e.value],
        })
        metrics.state_writes += 1
        metrics.task_completed = True
        log("orchestrator", "✓ Final summary written to global.final_summary")

    metrics.t_end = time.time()


if __name__ == "__main__":
    m = Metrics()
    try:
        run(m)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback; traceback.print_exc()

    banner("METRICS — WITH ROBOPOTATO")
    for k, v in m.report().items():
        print(f"  {k:<30} {v}")
    print()
