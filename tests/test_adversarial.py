"""
Adversarial test suite for Robopotato.

Pure programmatic tests — no Claude Code subprocess needed.
Each test is independent and self-describing.

Usage:
    python3 test_adversarial.py            # all tests
    python3 test_adversarial.py -v         # verbose
    python3 test_adversarial.py ExpiredToken  # single test by name

Robopotato must be running:
    cd .. && ROBOPOTATO_SECRET=test-secret cargo run
"""

import os
import sys
import time
import base64
import hmac as hmac_mod
import hashlib
import json
import threading
import unittest
import uuid
from datetime import datetime, timezone, timedelta

import httpx

BASE_URL = os.getenv("ROBOPOTATO_URL", "http://127.0.0.1:7878")
SECRET   = os.getenv("ROBOPOTATO_SECRET", "test-secret")

sys.path.insert(0, os.path.dirname(__file__))
from robopotato_client import RobopotatoClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _forge_token(claims: dict) -> str:
    """Sign an arbitrary claims dict with the known secret (for positive control tests)."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    sig = hmac_mod.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _tamper_token(token: str, new_claims: dict) -> str:
    """Re-encode claims without re-signing (produces an invalid token)."""
    payload = base64.urlsafe_b64encode(json.dumps(new_claims).encode()).rstrip(b"=").decode()
    _, original_sig = token.split(".", 1)
    return f"{payload}.{original_sig}"


def _client(role: str = "worker", name: str | None = None) -> RobopotatoClient:
    c = RobopotatoClient(BASE_URL)
    c.register(role=role, name=name)
    return c


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class ExpiredTokenTest(unittest.TestCase):
    """A token with expires_at in the past must be rejected."""

    def test_expired_token_replay(self):
        c = RobopotatoClient(BASE_URL)
        # Build a well-signed token with RFC3339 timestamps that expired 1 hour ago
        now = datetime.now(timezone.utc)
        claims = {
            "agent_id": "replay-attacker",
            "role": "orchestrator",
            "capabilities": [
                "state_read_global", "state_write_global",
                "state_read_shared", "state_write_shared",
                "state_read_own", "state_write_own",
                "agent_list", "agent_revoke", "token_verify",
            ],
            "issued_at":  _rfc3339(now - timedelta(hours=2)),
            "expires_at": _rfc3339(now - timedelta(hours=1)),
            "issuer": "robopotato",
        }
        expired_token = _forge_token(claims)
        c._client.headers.update({"Authorization": f"Bearer {expired_token}"})
        c.identity = type("I", (), {"token": expired_token, "agent_id": "replay-attacker", "role": "orchestrator", "expires_at": ""})()

        r = c._client.put(
            f"{BASE_URL}/state/shared.replay-test",
            json={"value": "pwned"},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        self.assertEqual(r.status_code, 401,
                         f"Expected 401 for expired token, got {r.status_code}: {r.text}")
        self.assertIn("expired", r.json().get("error", "").lower())


class TokenForgeryTest(unittest.TestCase):
    """A token signed with the wrong secret must be rejected."""

    def test_wrong_secret_forgery(self):
        now = int(time.time())
        claims = {
            "agent_id": "forger",
            "role": "orchestrator",
            "capabilities": ["StateWriteGlobal"],
            "issued_at":  now,
            "expires_at": now + 3600,
            "issuer": "robopotato",
        }
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        bad_sig = hmac_mod.new(b"wrong-secret", payload.encode(), hashlib.sha256).hexdigest()
        bad_token = f"{payload}.{bad_sig}"

        r = httpx.put(
            f"{BASE_URL}/state/global.forgery-test",
            json={"value": "hacked"},
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        self.assertEqual(r.status_code, 401,
                         f"Expected 401 for forged token, got {r.status_code}: {r.text}")


class TamperedTokenTest(unittest.TestCase):
    """Re-encoding claims without re-signing must be rejected."""

    def test_privilege_escalation_via_tamper(self):
        # Register as a worker
        c = _client("worker")
        worker_token = c.token

        # Decode the token, escalate role to orchestrator, keep original sig
        encoded, sig = worker_token.split(".", 1)
        padding = "=" * (4 - len(encoded) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded + padding))
        claims["role"] = "orchestrator"
        claims["capabilities"].append("StateWriteGlobal")

        tampered = _tamper_token(worker_token, claims)
        r = httpx.put(
            f"{BASE_URL}/state/global.escalation-test",
            json={"value": "escalated"},
            headers={"Authorization": f"Bearer {tampered}"},
        )
        self.assertEqual(r.status_code, 401,
                         f"Expected 401 for tampered token, got {r.status_code}: {r.text}")


class MissingAuthHeaderTest(unittest.TestCase):
    """Protected endpoints without Authorization header must return 401."""

    def test_no_auth_header(self):
        for method, url, body in [
            ("GET",    f"{BASE_URL}/state/shared.x",              None),
            ("PUT",    f"{BASE_URL}/state/shared.x",              {"value": 1}),
            ("DELETE", f"{BASE_URL}/state/shared.x",              None),
            ("GET",    f"{BASE_URL}/state/namespace/shared",      None),
        ]:
            r = httpx.request(method, url, json=body)
            self.assertEqual(r.status_code, 401,
                             f"{method} {url} should require auth, got {r.status_code}")


class CrossNamespacePollutionTest(unittest.TestCase):
    """Worker must not write to global.* or another agent's namespace."""

    def setUp(self):
        self.worker = _client("worker", name="pollution-worker")
        self.orch   = _client("orchestrator")

    def test_worker_cannot_write_global(self):
        ok, reason = self.worker.try_set_state("global.injected", "evil")
        self.assertFalse(ok, "Worker should not write to global.*")
        self.assertIn(str(ok), ["False"])

    def test_worker_cannot_write_other_agent_namespace(self):
        victim_id = self.orch.agent_id
        ok, reason = self.worker.try_set_state(f"agent.{victim_id}.secret", "stolen")
        self.assertFalse(ok, "Worker should not write to another agent's namespace")

    def test_worker_can_write_own_namespace(self):
        own_id = self.worker.agent_id
        ok, reason = self.worker.try_set_state(f"agent.{own_id}.data", {"x": 1})
        self.assertTrue(ok, f"Worker should write own namespace, got: {reason}")

    def test_worker_can_write_shared(self):
        ok, reason = self.worker.try_set_state("shared.collab", {"worker": True})
        self.assertTrue(ok, f"Worker should write shared.*, got: {reason}")

    def test_observer_cannot_write_anything(self):
        observer = _client("observer")
        for key in ["shared.obs-write", f"agent.{observer.agent_id}.own"]:
            ok, reason = observer.try_set_state(key, "nope")
            self.assertFalse(ok, f"Observer must not write to {key}")


class MidTaskRevocationTest(unittest.TestCase):
    """Revoked agent tokens must be rejected even if the token itself hasn't expired."""

    def test_revoke_blocks_subsequent_requests(self):
        orch   = _client("orchestrator")
        worker = _client("worker", name="to-be-revoked")

        # Worker can write initially
        ok, reason = worker.try_set_state("shared.before-revoke", "alive")
        self.assertTrue(ok, f"Pre-revoke write should succeed: {reason}")

        # Orchestrator revokes the worker
        orch.revoke(worker.agent_id)

        # Same token must now be rejected
        ok, reason = worker.try_set_state("shared.after-revoke", "ghost")
        self.assertFalse(ok, "Revoked agent must not write state")
        # Reason should indicate revocation or unauthorized
        self.assertTrue(
            any(kw in reason.lower() for kw in ("revoked", "unauthorized", "401")),
            f"Expected revocation error, got: {reason}",
        )

    def test_revoke_requires_orchestrator(self):
        orch   = _client("orchestrator")
        victim = _client("worker", name="victim")
        rogue  = _client("worker", name="rogue-revoker")

        # Worker trying to revoke another agent must fail
        r = rogue._client.delete(
            f"{BASE_URL}/agents/{victim.agent_id}",
            headers={"Authorization": f"Bearer {rogue.token}"},
        )
        self.assertIn(r.status_code, (401, 403),
                      f"Non-orchestrator revoke should be denied, got {r.status_code}")


class OCCStormTest(unittest.TestCase):
    """5 concurrent writers with expected_version — at most one should win per round."""

    def test_concurrent_writers_conflict(self):
        setup = _client("orchestrator")
        setup.set_state("shared.occ-counter", {"count": 0})
        initial = setup.get_state("shared.occ-counter")
        base_version = initial.version

        results = []
        errors  = []

        def try_write(idx: int):
            c = _client("worker", name=f"occ-worker-{idx}")
            ok, reason = c.try_set_state(
                "shared.occ-counter",
                {"count": idx},
                expected_version=base_version,
            )
            results.append((idx, ok, reason))

        threads = [threading.Thread(target=try_write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [(idx, ok, r) for idx, ok, r in results if ok]
        conflicts = [(idx, ok, r) for idx, ok, r in results if not ok]

        self.assertEqual(len(successes), 1,
                         f"Exactly one writer should win OCC storm, got {len(successes)}: {successes}")
        self.assertEqual(len(conflicts), 4,
                         f"Four writers should lose OCC storm, got {len(conflicts)}")
        for _, _, reason in conflicts:
            self.assertTrue(
                any(kw in reason.lower() for kw in ("conflict", "version", "409")),
                f"Conflict reason should mention version, got: {reason}",
            )


class PublicEndpointTest(unittest.TestCase):
    """Public endpoints must be reachable without auth."""

    def test_health_no_auth(self):
        r = httpx.get(f"{BASE_URL}/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_register_no_auth(self):
        r = httpx.post(f"{BASE_URL}/agents/register", json={"role": "worker"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.json())

    def test_verify_no_auth(self):
        # Register to get a valid token, then verify it without auth
        reg = httpx.post(f"{BASE_URL}/agents/register", json={"role": "worker"})
        token = reg.json()["token"]
        r = httpx.post(f"{BASE_URL}/tokens/verify", json={"token": token})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["valid"])


class InvalidTokenFormatTest(unittest.TestCase):
    """Malformed tokens must return 401, not 500."""

    def _expect_401(self, bad_token: str, label: str):
        r = httpx.get(
            f"{BASE_URL}/state/shared.x",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        self.assertEqual(r.status_code, 401,
                         f"[{label}] expected 401, got {r.status_code}: {r.text}")

    def test_empty_token(self):
        # An empty string is illegal as an HTTP header value; send a single dot instead.
        self._expect_401(".", "empty-like")

    def test_no_dot_separator(self):
        self._expect_401("nodottoken", "no-dot")

    def test_invalid_base64_payload(self):
        self._expect_401("!!!invalid!!!.fakesig", "bad-base64")

    def test_valid_base64_but_not_json(self):
        payload = base64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
        self._expect_401(f"{payload}.fakesig", "not-json")


class VersionTracking(unittest.TestCase):
    """Version numbers must increment correctly across writes."""

    def test_version_sequence(self):
        c = _client("orchestrator")
        key = f"shared.version-track-{uuid.uuid4().hex[:8]}"
        for expected_v in range(1, 6):
            c.set_state(key, {"v": expected_v})
            entry = c.get_state(key)
            self.assertEqual(entry.version, expected_v,
                             f"Expected version {expected_v}, got {entry.version}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def check_server():
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


if __name__ == "__main__":
    if not check_server():
        print(f"\n[ERROR] Robopotato not running at {BASE_URL}")
        print("Start it: cd .. && ROBOPOTATO_SECRET=test-secret cargo run")
        sys.exit(1)

    print(f"\nRobopotato adversarial test suite  [{BASE_URL}]")
    print("=" * 60)

    # Allow filtering by class name: python3 test_adversarial.py OCCStorm
    loader = unittest.TestLoader()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        suite = unittest.TestSuite()
        for name in sys.argv[1:]:
            suite.addTests(loader.loadTestsFromName(name, module=sys.modules[__name__]))
        sys.argv = sys.argv[:1]  # remove names so unittest doesn't also parse them
    else:
        suite = loader.loadTestsFromModule(sys.modules[__name__])

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
