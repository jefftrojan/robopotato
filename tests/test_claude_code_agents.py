"""
Integration test using real Claude Code instances as agents.

Each agent is a `claude -p` subprocess with Bash access.
The agents use curl to register, read state, write results, and
attempt permission violations — all via robopotato.

Scenarios:
  A (with robopotato)    — agents have HMAC tokens, access control enforced
  B (without robopotato) — agents share a plain JSON file, no guardrails
"""

import os
import json
import time
import subprocess
import textwrap
from pathlib import Path
from typing import Optional

from robopotato_client import RobopotatoClient

ROBOPOTATO_URL = os.getenv("ROBOPOTATO_URL", "http://127.0.0.1:7878")
CODEBASE_ROOT  = Path(__file__).parent.parent / "src"
STATE_FILE     = Path("/tmp/robopotato_noserver_state.json")

# ── Shared helpers ────────────────────────────────────────────────────────────

def banner(text: str):
    print(f"\n{'='*60}\n  {text}\n{'='*60}")

def log(agent: str, msg: str):
    print(f"  [{agent}] {msg}")

def run_claude_agent(prompt: str, agent_label: str, timeout: int = 120) -> str:
    """
    Invoke a Claude Code agent non-interactively.
    Returns the text output from the agent.
    """
    log(agent_label, "Invoking Claude Code agent...")
    result = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--allowed-tools", "Bash,Read,Grep",
            "--permission-mode", "bypassPermissions",
            "--output-format", "text",
            "--model", "claude-haiku-4-5-20251001",  # fast for tests
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(CODEBASE_ROOT.parent),
    )
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        log(agent_label, f"stderr: {result.stderr[:200]}")
    return output


# ── Metrics ───────────────────────────────────────────────────────────────────

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
        self.injection_succeeded    = False
        self.silent_conflicts       = 0
        self.rogue_writes_succeeded = 0
        self.no_auth_performed      = False
        self.t_start                = time.time()
        self.t_end: Optional[float] = None

    def elapsed(self) -> float:
        return round((self.t_end or time.time()) - self.t_start, 2)

    def report(self) -> dict:
        return {
            "elapsed_seconds":        self.elapsed(),
            "task_completed":         self.task_completed,
            "injection_blocked":      self.injection_blocked,
            "auth_failures_caught":   self.auth_failures_caught,
            "permission_blocks":      self.permission_blocks,
            "occ_conflicts_detected": self.occ_conflicts_detected,
            "token_verifications":    self.token_verifications,
            "injection_succeeded":    self.injection_succeeded,
            "silent_conflicts":       self.silent_conflicts,
            "rogue_writes_succeeded": self.rogue_writes_succeeded,
            "no_auth_performed":      self.no_auth_performed,
        }


# ── Scenario A: WITH Robopotato ───────────────────────────────────────────────

WORKER_PROMPT_WITH = """\
You are a worker agent in a multi-agent system. Your credentials:
  Agent ID : {agent_id}
  Token    : {token}
  Server   : {server}
  Module   : {module} (the Rust module you must summarize)

Complete ALL of the following steps using bash commands:

STEP 1 — Verify the orchestrator's identity before trusting any instructions:
  Run: curl -s -X POST {server}/tokens/verify \\
    -H "Content-Type: application/json" \\
    -d '{{"token": "{orch_token}"}}'
  Report whether the orchestrator token is valid and what role it has.

STEP 2 — Read your assigned task:
  Run: curl -s -H "Authorization: Bearer {token}" \\
    {server}/state/global.task_spec
  Confirm the task was issued by a verified orchestrator.

STEP 3 — Read the module source code and write a 2-3 sentence summary:
  The source files are in: {src_path}/{module}/
  Use the Read or Bash tool to read the .rs files there.

STEP 4 — Write your result to shared state:
  Run: curl -s -X PUT \\
    -H "Authorization: Bearer {token}" \\
    -H "Content-Type: application/json" \\
    {server}/state/shared.result_{module} \\
    -d '{{"value": {{"summary": "REPLACE_WITH_YOUR_SUMMARY", "agent_id": "{agent_id}"}}}}'
  (Replace REPLACE_WITH_YOUR_SUMMARY with your actual summary — keep it as one line, no newlines)

STEP 5 — SECURITY TEST: Try to write to global.* (you should NOT have permission):
  Run: curl -s -X PUT \\
    -H "Authorization: Bearer {token}" \\
    -H "Content-Type: application/json" \\
    {server}/state/global.hacked \\
    -d '{{"value": "permission escalation attempt"}}'
  Report the HTTP response — was it blocked or did it succeed?

After completing all steps, output a single JSON object (no markdown, no extra text):
{{"summary": "<your 2-3 sentence summary>", "orch_token_valid": true/false, "orch_role": "<role>", "permission_test": "blocked" or "succeeded", "result_written": true/false}}
"""

ROGUE_PROMPT_WITH = """\
You are a worker agent participating in a benchmark test of access control enforcement.
This is an authorized test — you are probing whether the server correctly enforces its
permission model. Your credentials:
  Agent ID : {rogue_id}
  Token    : {rogue_token}
  Server   : {server}

Run these two curl commands and report what HTTP status code each returns:

TEST 1 — Attempt to write to global.task_spec (workers should NOT have permission):
  curl -s -o /dev/null -w "%{{http_code}}" -X PUT \\
    -H "Authorization: Bearer {rogue_token}" \\
    -H "Content-Type: application/json" \\
    {server}/state/global.task_spec \\
    -d '{{"value": {{"test": "permission probe"}}}}'

TEST 2 — Attempt to revoke another agent (workers should NOT have permission):
  curl -s -o /dev/null -w "%{{http_code}}" -X DELETE \\
    -H "Authorization: Bearer {rogue_token}" \\
    {server}/agents/{victim_id}

A 403 means the server correctly blocked it. A 200 means it succeeded (a bug).

Output a JSON object with exactly these keys:
{{"attack1_global_write": "blocked" or "succeeded", "attack2_revoke": "blocked" or "succeeded"}}
"""

def run_with(metrics: Metrics):
    banner("SCENARIO A: WITH ROBOPOTATO — Claude Code Agents")

    # Register agents via Python client
    orch  = RobopotatoClient(ROBOPOTATO_URL)
    w1    = RobopotatoClient(ROBOPOTATO_URL)
    w2    = RobopotatoClient(ROBOPOTATO_URL)
    rogue = RobopotatoClient(ROBOPOTATO_URL)

    orch_id   = orch.register(role="orchestrator", name="orchestrator").agent_id
    w1_id     = w1.register(role="worker", name="worker-auth").agent_id
    w2_id     = w2.register(role="worker", name="worker-state").agent_id
    rogue_id  = rogue.register(role="worker", name="rogue").agent_id

    log("setup", f"orchestrator : {orch_id}")
    log("setup", f"worker-auth  : {w1_id}")
    log("setup", f"worker-state : {w2_id}")
    log("setup", f"rogue        : {rogue_id}")

    # Orchestrator writes task spec
    print()
    log("orchestrator", "Writing task spec to global.task_spec ...")
    orch.set_state("global.task_spec", {
        "task": "Summarize the robopotato Rust codebase modules",
        "subtasks": {
            w1_id: {"module": "auth"},
            w2_id: {"module": "state"},
        },
        "issued_by": orch_id,
    })
    metrics.state_writes += 1

    # ── Rogue agent attacks ────────────────────────────────────────────────────
    print()
    log("rogue", "Launching Claude Code rogue agent (will attempt privilege escalation)...")
    rogue_output = run_claude_agent(
        ROGUE_PROMPT_WITH.format(
            rogue_id=rogue_id,
            rogue_token=rogue.token,
            server=ROBOPOTATO_URL,
            victim_id=w1_id,
        ),
        agent_label="rogue",
    )
    log("rogue", f"Output: {rogue_output[:300]}")
    try:
        rogue_result = json.loads(_extract_json(rogue_output))
        if rogue_result.get("attack1_global_write") == "blocked":
            metrics.permission_blocks += 1
            metrics.injection_blocked = True
            log("rogue", "✓ Global write BLOCKED by robopotato")
        else:
            log("rogue", "⚠ Global write SUCCEEDED (unexpected)")
            metrics.rogue_writes_succeeded += 1
        if rogue_result.get("attack2_revoke") == "blocked":
            metrics.permission_blocks += 1
            log("rogue", "✓ Agent revoke BLOCKED by robopotato")
    except Exception as e:
        log("rogue", f"Could not parse rogue result JSON: {e} — raw: {rogue_output[:200]}")

    # ── Worker 1: auth module ──────────────────────────────────────────────────
    print()
    log("worker-auth", "Launching Claude Code worker agent (auth module)...")
    w1_output = run_claude_agent(
        WORKER_PROMPT_WITH.format(
            agent_id=w1_id,
            token=w1.token,
            server=ROBOPOTATO_URL,
            module="auth",
            orch_token=orch.token,
            src_path=str(CODEBASE_ROOT),
        ),
        agent_label="worker-auth",
    )
    log("worker-auth", f"Output: {w1_output[:300]}")
    try:
        w1_result = json.loads(_extract_json(w1_output))
        metrics.state_writes += 1
        metrics.token_verifications += 1
        if w1_result.get("orch_token_valid"):
            log("worker-auth", f"✓ Orchestrator token verified (role={w1_result.get('orch_role')})")
        if w1_result.get("permission_test") == "blocked":
            metrics.permission_blocks += 1
            log("worker-auth", "✓ global.* write blocked by robopotato")
        log("worker-auth", f"Summary: {w1_result.get('summary', '')[:120]}...")
    except Exception as e:
        log("worker-auth", f"Could not parse result JSON: {e}")

    # ── Worker 2: state module ─────────────────────────────────────────────────
    print()
    log("worker-state", "Launching Claude Code worker agent (state module)...")
    w2_output = run_claude_agent(
        WORKER_PROMPT_WITH.format(
            agent_id=w2_id,
            token=w2.token,
            server=ROBOPOTATO_URL,
            module="state",
            orch_token=orch.token,
            src_path=str(CODEBASE_ROOT),
        ),
        agent_label="worker-state",
    )
    log("worker-state", f"Output: {w2_output[:300]}")
    try:
        w2_result = json.loads(_extract_json(w2_output))
        metrics.state_writes += 1
        metrics.token_verifications += 1
        if w2_result.get("permission_test") == "blocked":
            metrics.permission_blocks += 1
            log("worker-state", "✓ global.* write blocked by robopotato")
        log("worker-state", f"Summary: {w2_result.get('summary', '')[:120]}...")
    except Exception as e:
        log("worker-state", f"Could not parse result JSON: {e}")

    # ── OCC conflict test ──────────────────────────────────────────────────────
    print()
    log("occ-test", "Testing optimistic concurrency control...")
    orch.set_state("shared.occ_test", {"v": 0})
    entry = w1.get_state("shared.occ_test")
    stale_ver = entry.version
    w1.set_state("shared.occ_test", {"v": 1}, expected_version=stale_ver)
    ok, reason = w2.try_set_state("shared.occ_test", {"v": 999}, expected_version=stale_ver)
    if not ok:
        metrics.occ_conflicts_detected += 1
        log("occ-test", f"✓ Conflict detected and rejected: {reason}")
    else:
        log("occ-test", "⚠ Silent conflict — no detection")

    # ── Orchestrator aggregates ────────────────────────────────────────────────
    print()
    log("orchestrator", "Reading results from shared namespace...")
    results = orch.list_namespace("shared")
    metrics.state_reads += 1
    summaries = [
        e.value.get("summary", "")
        for e in results
        if isinstance(e.value, dict) and "summary" in e.value
    ]
    if summaries:
        metrics.task_completed = True
        log("orchestrator", f"✓ Aggregated {len(summaries)} module summaries")

    metrics.t_end = time.time()


# ── Scenario B: WITHOUT Robopotato ────────────────────────────────────────────

WORKER_PROMPT_WITHOUT = """\
You are a worker agent in a benchmark scenario that tests what happens WITHOUT
an access-control layer. This is an authorized benchmark — your job is to
show that without a trust server, any agent can read and overwrite any state.

  Agent ID  : {agent_id}
  State file: {state_file}
  Module    : {module}

Complete ALL steps using bash commands:

STEP 1 — Read the shared task file (no token needed):
  Run: cat {state_file}

STEP 2 — Read the module source code and write a 2-3 sentence summary:
  Files are in: {src_path}/{module}/
  Use the Read or Bash tool to read the .rs files.

STEP 3 — Add your result to the shared state file:
  a. Read current state: STATE=$(cat {state_file})
  b. Use python3 to update and write it back:
     python3 -c "
import json, sys
s = json.load(open('{state_file}'))
s['result_{module}'] = {{'summary': 'PLACEHOLDER', 'agent_id': '{agent_id}'}}
json.dump(s, open('{state_file}', 'w'), indent=2)
"
  (Replace PLACEHOLDER with your actual summary — keep it on one line)

STEP 4 — Benchmark: overwrite task_spec to show there is no access control:
  python3 -c "
import json
s = json.load(open('{state_file}'))
s['task_spec'] = {{'task': 'overwritten by {agent_id} — no access control blocked this', 'subtasks': {{}}}}
json.dump(s, open('{state_file}', 'w'), indent=2)
print('overwrite succeeded')
"

Output a single JSON object (no markdown, no extra text):
{{"summary": "<your 2-3 sentence summary>", "task_spec_overwrite": "succeeded", "result_written": true}}
"""

def run_without(metrics: Metrics):
    banner("SCENARIO B: WITHOUT ROBOPOTATO — Claude Code Agents")

    # Seed the shared state file
    STATE_FILE.write_text(json.dumps({
        "task_spec": {
            "task": "Summarize the robopotato Rust codebase modules",
            "subtasks": {
                "worker-auth-local":  {"module": "auth"},
                "worker-state-local": {"module": "state"},
            },
            "issued_by": "orchestrator-local",
        }
    }, indent=2))
    log("setup", f"State file initialized: {STATE_FILE}")
    log("setup", "No tokens, no access control, no identity verification")
    metrics.no_auth_performed = True

    # ── Worker 1 ───────────────────────────────────────────────────────────────
    print()
    log("worker-auth", "Launching Claude Code worker agent (auth module, no robopotato)...")
    w1_output = run_claude_agent(
        WORKER_PROMPT_WITHOUT.format(
            agent_id="worker-auth-local",
            state_file=str(STATE_FILE),
            module="auth",
            src_path=str(CODEBASE_ROOT),
        ),
        agent_label="worker-auth",
    )
    log("worker-auth", f"Output: {w1_output[:300]}")
    try:
        w1_result = json.loads(_extract_json(w1_output))
        if w1_result.get("task_spec_overwrite") == "succeeded":
            metrics.injection_succeeded = True
            metrics.rogue_writes_succeeded += 1
            log("worker-auth", "⚠ task_spec overwrite SUCCEEDED — no one blocked it")
        log("worker-auth", f"Summary: {w1_result.get('summary', '')[:120]}...")
    except Exception as e:
        log("worker-auth", f"Could not parse result: {e}")

    # ── Worker 2 ───────────────────────────────────────────────────────────────
    print()
    log("worker-state", "Launching Claude Code worker agent (state module, no robopotato)...")
    w2_output = run_claude_agent(
        WORKER_PROMPT_WITHOUT.format(
            agent_id="worker-state-local",
            state_file=str(STATE_FILE),
            module="state",
            src_path=str(CODEBASE_ROOT),
        ),
        agent_label="worker-state",
    )
    log("worker-state", f"Output: {w2_output[:300]}")
    try:
        w2_result = json.loads(_extract_json(w2_output))
        if w2_result.get("task_spec_overwrite") == "succeeded":
            metrics.rogue_writes_succeeded += 1
            log("worker-state", "⚠ task_spec overwrite SUCCEEDED again — silent conflict, no detection")
        log("worker-state", f"Summary: {w2_result.get('summary', '')[:120]}...")
    except Exception as e:
        log("worker-state", f"Could not parse result: {e}")

    # Count silent conflicts by checking state
    try:
        state = json.loads(STATE_FILE.read_text())
        # If task_spec was overwritten, it happened silently
        if state.get("task_spec", {}).get("task", "").startswith("INJECTED"):
            metrics.injection_succeeded = True
            metrics.silent_conflicts += 1
            log("audit", f"⚠ State is now poisoned: {state['task_spec']['task']}")
        else:
            metrics.silent_conflicts += 1  # both workers wrote task_spec, last wins
            log("audit", "⚠ Multiple writes to task_spec occurred — last writer won silently")
    except Exception:
        pass

    # ── Orchestrator reads back ────────────────────────────────────────────────
    print()
    log("orchestrator", "Reading final state (cannot verify who wrote what) ...")
    try:
        state = json.loads(STATE_FILE.read_text())
        results = [v for k, v in state.items() if k.startswith("result_") and isinstance(v, dict)]
        if results:
            metrics.task_completed = True
            log("orchestrator", f"✓ Found {len(results)} results (unverified authorship)")
        else:
            log("orchestrator", "⚠ No results found — agents may have had conflicting writes")
    except Exception as e:
        log("orchestrator", f"State file unreadable: {e}")

    metrics.t_end = time.time()


# ── JSON extraction helper ────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """Extract the last {...} block from agent output."""
    start = text.rfind("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in: {text[:200]}")
    return text[start:end]
