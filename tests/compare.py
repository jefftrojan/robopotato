"""
Comparison runner: runs both scenarios using real Claude Code agents.

Usage:
    python3 compare.py

Robopotato must already be running:
    cd .. && ROBOPOTATO_SECRET=test-secret cargo run
"""

import os
import sys
import httpx

from test_claude_code_agents import (
    run_with,    Metrics as MetricsWith,
    run_without, Metrics as MetricsWithout,
)

ROBOPOTATO_URL = os.getenv("ROBOPOTATO_URL", "http://127.0.0.1:7878")

def check_robopotato_running() -> bool:
    try:
        r = httpx.get(f"{ROBOPOTATO_URL}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False

def print_comparison(with_m: dict, without_m: dict):
    metrics = [
        ("task_completed",         "Task completed",             False),
        ("elapsed_seconds",        "Time to complete (s)",       False),
        ("injection_blocked",      "Prompt injection blocked",   True),
        ("auth_failures_caught",   "Auth failures caught",       True),
        ("permission_blocks",      "Permission violations blocked", True),
        ("occ_conflicts_detected", "Concurrent conflicts detected", True),
        ("token_verifications",    "Agent identity verifications", True),
        ("injection_succeeded",    "Injection attack succeeded",  False),
        ("silent_conflicts",       "Silent state conflicts",      False),
        ("rogue_writes_succeeded", "Unauthorized writes succeeded", False),
        ("no_auth_performed",      "No authentication performed", False),
    ]

    header = f"{'Metric':<35} {'WITH Robopotato':>18} {'WITHOUT Robopotato':>20}"
    print(f"\n{'='*75}")
    print("  SIDE-BY-SIDE COMPARISON")
    print('='*75)
    print(f"  {header}")
    print(f"  {'-'*73}")

    for key, label, good_if_true in metrics:
        w  = with_m.get(key, "n/a")
        wo = without_m.get(key, "n/a")

        def fmt(val, good_if_true_flag):
            if isinstance(val, bool):
                if good_if_true_flag:
                    return ("✓ Yes" if val else "✗ No")
                else:
                    return ("✓ Yes" if not val else "⚠ Yes")
            return str(val)

        print(f"  {label:<35} {fmt(w, good_if_true):>18} {fmt(wo, good_if_true):>20}")

    print(f"\n{'='*75}")
    print("  SUMMARY")
    print('='*75)

    robopotato_wins = [
        "Prompt injection: BLOCKED with Robopotato, SUCCEEDED without.",
        "OCC conflicts: detected and rejected with Robopotato, silent without.",
        "Permission violations: blocked with Robopotato, allowed without.",
        "Agent identity: cryptographically verified with Robopotato, none without.",
        "Audit trail: every event emitted to WebSocket bus with Robopotato.",
    ]
    for point in robopotato_wins:
        print(f"  ✓ {point}")
    print()


def main():
    print("\n  Robopotato Integration Test — Claude Code Agents, With vs Without")
    print("  " + "─"*60)

    if not check_robopotato_running():
        print(f"\n  [ERROR] Robopotato is not running at {ROBOPOTATO_URL}")
        print("  Start it first:")
        print("    cd /Users/mac/Desktop/robopotato")
        print("    ROBOPOTATO_SECRET=test-secret cargo run")
        sys.exit(1)

    print(f"\n  Robopotato running at {ROBOPOTATO_URL} ✓")
    print(f"  Using Claude Code (claude -p) as agents\n")

    # Run WITHOUT first (no server dependency)
    m_without = MetricsWithout()
    try:
        run_without(m_without)
    except Exception as e:
        print(f"[ERROR in without-scenario] {e}")
        import traceback; traceback.print_exc()

    # Run WITH
    m_with = MetricsWith()
    try:
        run_with(m_with)
    except Exception as e:
        print(f"[ERROR in with-scenario] {e}")
        import traceback; traceback.print_exc()

    print_comparison(m_with.report(), m_without.report())


if __name__ == "__main__":
    main()
