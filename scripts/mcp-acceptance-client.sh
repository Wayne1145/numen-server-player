#!/usr/bin/env bash
# S4 acceptance driver: an INDEPENDENT MCP client (bash + curl only — no Minecraft classpath,
# no model API key) that drives the server-side MCP endpoint through the full acceptance flow:
#   initialize -> notifications/initialized -> tools/list -> create/list companions
#   -> acquire_control -> goto -> task_status (poll) -> get_self_status -> release_control
# plus negative checks (no token -> 401, query-string token -> 401).
#
# Usage: bash scripts/s4-mcp-client.sh [url] [token] [companion]
set -uo pipefail
URL="${1:-http://127.0.0.1:25567/mcp}"
TOKEN="${2:?usage: $0 <url> <token> [companion]}"
COMP="${3:-McpBot}"

rpc() {  # rpc <id> <method> [params-json]
  local id="$1" method="$2" params="${3:-}"
  local body="{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"$method\"$( [ -n "$params" ] && echo ",\"params\":$params" )}"
  curl -s -m 15 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$body" "$URL"
}
call() { rpc "$1" tools/call "{\"name\":\"$2\",\"arguments\":$3}"; }
text() { python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result']['content'][0]['text'])" 2>/dev/null; }

pass=1
chk() { if [ "$2" = "$3" ] || { [ "$2" = "CONTAINS" ] && echo "$4" | grep -q "$3"; }; then echo "  PASS: $1"; else echo "  FAIL: $1 (got: ${4:-$3})"; pass=0; fi; }

echo "### S4 MCP client vs $URL ###"

echo "--- negative: auth ---"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' "$URL")
chk "no bearer -> 401" "$code" "401"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' "$URL?token=$TOKEN")
chk "query-string token -> 401" "$code" "401"

echo "--- initialize / handshake ---"
init=$(rpc 1 initialize '{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"s4-test","version":"1"}}')
chk "initialize returns serverInfo" CONTAINS "numen-server-mcp" "$init"
note_code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' "$URL")
chk "notifications/initialized -> 202" "$note_code" "202"

echo "--- tools/list ---"
tl=$(rpc 2 tools/list)
chk "lists acquire_control" CONTAINS "acquire_control" "$tl"
chk "lists goto (engine tool, capability-filtered)" CONTAINS '"goto"' "$tl"
chk "does NOT list mine (destructive off)" "$(echo "$tl" | grep -c '\"mine\"')" "0"

echo "--- companions ---"
call 3 create_companion "{\"name\":\"$COMP\"}" >/dev/null; sleep 2
lc=$(call 4 list_companions '{}')
chk "companion listed" CONTAINS "$COMP" "$lc"

echo "--- acquire_control ---"
acq=$(call 5 acquire_control "{\"companion\":\"$COMP\"}")
LEASE=$(echo "$acq" | python3 -c "import sys,json; r=json.load(sys.stdin); print(json.loads(r['result']['content'][0]['text'])['lease_id'])" 2>/dev/null)
if [ -n "${LEASE:-}" ]; then echo "  PASS: lease acquired: $LEASE"; else echo "  FAIL: no lease ($acq)"; pass=0; fi

echo "--- goto (async body action) ---"
st=$(call 6 get_self_status "{\"companion\":\"$COMP\"}" | text)
X=$(echo "$st" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['position']['x']))" 2>/dev/null)
Z=$(echo "$st" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['position']['z']))" 2>/dev/null)
echo "  start pos: x=$X z=$Z"
go=$(call 7 goto "{\"companion\":\"$COMP\",\"lease_id\":\"$LEASE\",\"x\":$((X+6)),\"z\":$((Z+3))}")
chk "goto dispatched with task_id" CONTAINS "task_id" "$go"

echo "--- task_status poll until idle ---"
state="?"
for i in $(seq 1 20); do
  ts=$(call $((10+i)) task_status "{\"companion\":\"$COMP\"}" | text)
  state=$(echo "$ts" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])" 2>/dev/null)
  echo "  poll $i: $state"
  [ "$state" = "idle" ] && break
  sleep 2
done
chk "task reached idle" "$state" "idle"

echo "--- perceive: position changed ---"
st2=$(call 40 get_self_status "{\"companion\":\"$COMP\"}" | text)
X2=$(echo "$st2" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['position']['x']))" 2>/dev/null)
Z2=$(echo "$st2" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['position']['z']))" 2>/dev/null)
echo "  end pos: x=$X2 z=$Z2 (was x=$X z=$Z)"
if [ "$X2" != "$X" ] || [ "$Z2" != "$Z" ]; then echo "  PASS: body moved"; else echo "  FAIL: body did not move"; pass=0; fi

echo "--- release_control ---"
rel=$(call 41 release_control "{\"companion\":\"$COMP\",\"lease_id\":\"$LEASE\"}")
chk "released" CONTAINS "released" "$rel"

echo "--- result ---"
if [ "$pass" = 1 ]; then echo "S4 MCP CLIENT: ALL PASS"; exit 0; else echo "S4 MCP CLIENT: FAIL"; exit 1; fi
