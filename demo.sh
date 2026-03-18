#!/usr/bin/env bash
# robopotato live demo — shows what the server blocks and why
# Usage: ./demo.sh
# Requires: robopotato running  →  ROBOPOTATO_SECRET=test-secret cargo run

set -euo pipefail

BASE="${ROBOPOTATO_URL:-http://127.0.0.1:7878}"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}▶ $*${RESET}"; }
ok()    { echo -e "  ${GREEN}✓ $*${RESET}"; }
block() { echo -e "  ${RED}✗ BLOCKED — $*${RESET}"; }
info()  { echo -e "  ${DIM}$*${RESET}"; }
sep()   { echo -e "${DIM}$(printf '─%.0s' {1..60})${RESET}"; }

# ── Helpers ──────────────────────────────────────────────────────────────────
register() {
  curl -sf -X POST "$BASE/agents/register" \
    -H "Content-Type: application/json" \
    -d "{\"role\":\"$1\",\"name\":\"$2\"}"
}

get_token()    { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])"; }
get_agent_id() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_id'])"; }

http_put() {   # token key payload → http_status
  curl -s -o /dev/null -w "%{http_code}" -X PUT "$BASE/state/$2" \
    -H "Authorization: Bearer $1" \
    -H "Content-Type: application/json" \
    -d "$3"
}

http_delete_agent() {  # token agent_id → http_status
  curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/agents/$2" \
    -H "Authorization: Bearer $1"
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  robopotato — live security demo${RESET}"
sep
echo -e "  Server: ${CYAN}$BASE${RESET}"
echo ""

# Check server is up
if ! curl -sf "$BASE/health" > /dev/null; then
  echo -e "${RED}  ERROR: robopotato is not running at $BASE${RESET}"
  echo -e "  Start it:  ${DIM}ROBOPOTATO_SECRET=test-secret cargo run${RESET}"
  exit 1
fi
ok "Server is healthy"

# ── 1. Register agents ────────────────────────────────────────────────────────
sep
step "Registering agents"

ORCH_JSON=$(register orchestrator "demo-orchestrator")
ORCH_TOKEN=$(get_token "$ORCH_JSON")
ORCH_ID=$(get_agent_id "$ORCH_JSON")
ok "Orchestrator registered  (id: ${DIM}${ORCH_ID:0:8}...${RESET}${GREEN})"

WORKER_JSON=$(register worker "demo-worker")
WORKER_TOKEN=$(get_token "$WORKER_JSON")
WORKER_ID=$(get_agent_id "$WORKER_JSON")
ok "Worker registered         (id: ${DIM}${WORKER_ID:0:8}...${RESET}${GREEN})"

OBS_JSON=$(register observer "demo-observer")
OBS_TOKEN=$(get_token "$OBS_JSON")
OBS_ID=$(get_agent_id "$OBS_JSON")
ok "Observer registered       (id: ${DIM}${OBS_ID:0:8}...${RESET}${GREEN})"

ROGUE_JSON=$(register worker "rogue-agent")
ROGUE_TOKEN=$(get_token "$ROGUE_JSON")
ROGUE_ID=$(get_agent_id "$ROGUE_JSON")
ok "Rogue worker registered   (id: ${DIM}${ROGUE_ID:0:8}...${RESET}${GREEN})"

# ── 2. Namespace isolation ────────────────────────────────────────────────────
sep
step "Namespace isolation — workers cannot write global.*"

STATUS=$(http_put "$ORCH_TOKEN" "global.config" '{"value":{"model":"gpt-4o","max_tokens":2048}}')
ok "Orchestrator writes global.config → HTTP $STATUS"

STATUS=$(http_put "$WORKER_TOKEN" "global.config" '{"value":"overwritten!"}')
if [[ "$STATUS" == "403" ]]; then
  block "Worker PUT global.config → HTTP $STATUS (missing capability: state_write_global)"
else
  echo -e "  ${RED}UNEXPECTED: got HTTP $STATUS${RESET}"
fi

# ── 3. Observer cannot write anywhere ─────────────────────────────────────────
sep
step "Observer is read-only — cannot write shared.*"

STATUS=$(http_put "$OBS_TOKEN" "shared.results" '{"value":"observer-write"}')
if [[ "$STATUS" == "403" ]]; then
  block "Observer PUT shared.results → HTTP $STATUS (missing capability: state_write_shared)"
else
  echo -e "  ${RED}UNEXPECTED: got HTTP $STATUS${RESET}"
fi

# ── 4. Cross-agent namespace isolation ────────────────────────────────────────
sep
step "Agent namespace isolation — workers cannot write each other's state"

STATUS=$(http_put "$WORKER_TOKEN" "agent.$ORCH_ID.private" '{"value":"stolen"}')
if [[ "$STATUS" == "403" ]]; then
  block "Worker PUT agent.<orchestrator>.private → HTTP $STATUS (not the owner)"
else
  echo -e "  ${RED}UNEXPECTED: got HTTP $STATUS${RESET}"
fi

STATUS=$(http_put "$WORKER_TOKEN" "agent.$WORKER_ID.private" '{"value":"my-own-data"}')
ok "Worker PUT agent.<self>.private → HTTP $STATUS (owner allowed)"

# ── 5. Optimistic concurrency control ─────────────────────────────────────────
sep
step "Optimistic Concurrency Control — concurrent writers, one wins"

# Seed the key at version 1
http_put "$ORCH_TOKEN" "shared.task" '{"value":{"status":"pending"}}' > /dev/null

STATUS_A=$(http_put "$WORKER_TOKEN" "shared.task" '{"value":{"status":"done-A"},"expected_version":1}')
STATUS_B=$(http_put "$ROGUE_TOKEN"  "shared.task" '{"value":{"status":"done-B"},"expected_version":1}')

if [[ "$STATUS_A" == "200" && "$STATUS_B" == "409" ]]; then
  ok   "Worker A writes shared.task (expected_version=1) → HTTP $STATUS_A"
  block "Worker B writes shared.task (expected_version=1) → HTTP $STATUS_B (version conflict: stale read detected)"
elif [[ "$STATUS_B" == "200" && "$STATUS_A" == "409" ]]; then
  ok   "Worker B wins shared.task (expected_version=1) → HTTP $STATUS_B"
  block "Worker A conflict (expected_version=1) → HTTP $STATUS_A (version conflict: stale read detected)"
else
  echo -e "  ${YELLOW}Both wrote (race — re-run for sequential result): A=$STATUS_A B=$STATUS_B${RESET}"
fi

# ── 6. Mid-task revocation ─────────────────────────────────────────────────────
sep
step "Mid-task revocation — rogue agent blocked after orchestrator revokes"

STATUS=$(http_put "$ROGUE_TOKEN" "shared.pre-revoke" '{"value":"alive"}')
ok "Rogue agent writes before revocation → HTTP $STATUS"

REVOKE_STATUS=$(http_delete_agent "$ORCH_TOKEN" "$ROGUE_ID")
ok "Orchestrator revokes rogue agent → HTTP $REVOKE_STATUS"

STATUS=$(http_put "$ROGUE_TOKEN" "shared.post-revoke" '{"value":"still-here?"}')
if [[ "$STATUS" == "401" ]]; then
  block "Rogue agent write after revocation → HTTP $STATUS (agent has been revoked)"
else
  echo -e "  ${RED}UNEXPECTED: got HTTP $STATUS${RESET}"
fi

# ── 7. Token forgery ──────────────────────────────────────────────────────────
sep
step "Token forgery — wrong HMAC secret is rejected"

# Craft a plausible-looking token with a bad signature
FAKE_PAYLOAD=$(echo '{"agent_id":"hacker","role":"orchestrator","capabilities":["state_write_global"],"issued_at":"2025-01-01T00:00:00+00:00","expires_at":"2099-01-01T00:00:00+00:00","issuer":"robopotato"}' \
  | python3 -c "import sys,base64; print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).rstrip(b'=').decode())")
FAKE_TOKEN="${FAKE_PAYLOAD}.deadbeefdeadbeefdeadbeef00000000deadbeefdeadbeefdeadbeef00000000"

STATUS=$(http_put "$FAKE_TOKEN" "global.pwned" '{"value":"hacked"}')
if [[ "$STATUS" == "401" ]]; then
  block "Forged token PUT global.pwned → HTTP $STATUS (invalid token signature)"
else
  echo -e "  ${RED}UNEXPECTED: got HTTP $STATUS${RESET}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
sep
echo ""
echo -e "${BOLD}  Summary${RESET}"
echo ""
echo -e "  ${GREEN}✓${RESET}  Namespace isolation    workers cannot touch global.* or other agents' namespaces"
echo -e "  ${GREEN}✓${RESET}  Role enforcement       observers are permanently read-only"
echo -e "  ${GREEN}✓${RESET}  OCC conflict detection concurrent stale writes produce 409, not silent overwrites"
echo -e "  ${GREEN}✓${RESET}  Mid-task revocation    revoked tokens rejected immediately, no restart needed"
echo -e "  ${GREEN}✓${RESET}  Token forgery rejected wrong HMAC secret → 401 every time"
echo ""
echo -e "  All of the above enforced at the infrastructure layer."
echo -e "  ${DIM}No prompt-engineering required.${RESET}"
echo ""
