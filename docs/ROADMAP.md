# PLAN — Pure server-side Numen "AI real player" fork

Authoritative staged plan. Each stage: **write failing test → minimal impl → full build →
run tests → boot an isolated test server → save logs → stage report → git commit → STOP
and report → wait for explicit go-ahead.** No cross-stage work. No hot-reload. Production
server is off-limits until final human approval (see `README.DEV.md` §5).

## Target behaviour (definition of "AI real player")

A true `ServerPlayer` companion: appears like a normal player, shows in Tab, has native
collision/gravity/fall/hunger/air/burn/potion/death/respawn, uses native inventory /
equipment / XP / playerdata, auto-picks up drops, moves/jumps/swims/climbs/opens
doors/pathfinds, mines/places/attacks/uses items, opens containers and manipulates
inventory **through native menu click paths** (no direct block/NBT/item injection).
Driven by an LLM API **and** by custom MCP tools — MCP alone (no model API) must fully
control it. No client mod required.

## Architecture invariants (all stages)

- Keep/reuse Numen's `NumenPlayer extends ServerPlayer`, `TaskQueue`,
  `NumenTool.onServerCall`, `CompanionRegistry`, `TaskDispatch`, `CompanionTickDispatcher`.
- New pure **server** control layer; replace client `NumenActuator` with
  `ServerNumenActuator`; add a server-side MCP server.
- API-agent path and MCP path converge on the **same** actuator and share: permission ·
  task lock · control lease · action budget · dimension/range limits · namespace policy ·
  audit · cancellation · death/unload/shutdown cleanup. **MCP must not bypass security.**
- HTTP/MCP worker threads do auth/parse/ratelimit/future-orchestration only; **all** world/
  entity/registry/menu/task access hops to the main thread via `minecraftServer.execute(...)`.
- Dedicated server must never load `net.minecraft.client.*`, `Minecraft.getInstance()`,
  `AbstractClientPlayer`, `ClientNumenLookup`, `NumenRoster`, `AgentLoopRegistry`,
  `ClientToolContext`. Client content lives in an optional client source-set/module.

---

## S0 — Fork & baseline  ✅ (in progress)
Create lab, clone+pin upstream, provenance/license/plan, baseline build, blank Forge
test-env. **Accept:** clean build; original baseline tests pass; dedicated test env set
up; all paths recorded. No production contact.

## S1 — Dedicated-server body
Dedicated server loads no client-only classes; create/leave a real `NumenPlayer`;
PlayerList/Tab/entity-tracking; gravity/pickup/PlayerData; death & respawn; basic
join/leave/status admin commands. No MCP, no LLM.
**Accept (on Forge test server):** AI player visible; auto-pickup; recovers after death;
playerdata survives restart; **no KeepAlive/Channel/client-class errors.**
Replace the silent `try{ doTick() } catch(Exception ignored){}` with rate-limited logging
(companion UUID + task + exception type), a consecutive-error threshold that pauses the
companion + stops its task, and a `degraded` status visible to `get_self_status`. Never
crash the whole server for one companion; never hide errors.

## S2 — ServerActuator
New `ServerNumenActuator` (no `NumenActuator`, no C→S payload); main-thread dispatch;
owner/principal verification; task queue works. Verify read, move, mine, container read,
item transfer.
**Accept (standalone Forge server, no client mod):** via test command / simulated
principal, successfully run `goto` / `mine` / `inspect_gui` / `transfer`.

## S3 — Control & security
Principal · Token · lease · tool-capability whitelist · rate-limit · audit · action budget
· death/delete/shutdown cleanup. Capabilities: `SERVER_READ_ONLY`, `SERVER_BODY_ACTION`,
`CLIENT_ONLY`, `INTERNAL_ONLY`, `DESTRUCTIVE`, `ADMIN_ONLY`. Roles: `admin`/`owner`/
`readonly`. Default policy: overworld only; `minecraft` namespace only; destructive/place/
attack OFF; distance/duration/tool-call/per-player-task/global-task caps.
**Accept:** two principals contend → only one gets write control; stale-lease requests
rejected (fencing token); readonly cannot modify world; expired lease auto-releases; every
call audited.

## S4 — MCP
Streamable HTTP (JSON-RPC 2.0) MCP server + stdio adapter. Correct `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`; strict validation; task
start/status/stop; Bearer auth (`enabled=false` default, bind `127.0.0.1`/`::1`, 256-bit
token, constant-time compare, no query-string token, refuse non-loopback bind w/o auth,
no `0.0.0.0` default); body ≤64 KiB, batch ≤16, bounded worker pool/queue, rate limits,
request-vs-task timeout split. Hermetic MCP test client.
**Accept:** external MCP client does initialize → tools/list → acquire_control → goto →
task_status → release_control; **works with no model API key present.**

## S5 — Dual entry (API + MCP)
Responses/OpenAI agent adapter; both API and MCP agents call `ServerNumenActuator`; same
lease model; no double control; API keys never logged; cost/call caps; cancellable.
**Accept:** API path and MCP path execute the same tool with identical audit/permission
results.

## S6 — Compatibility acceptance (strict order)
Blank Forge → blank Mohist → private modpack modpack **copy** → private modpack world **copy**. Never the
production world. Focus mods: Refined Storage, Tom's Storage, Sophisticated Backpacks,
Iron Chests, TACZ, Touhou Little Maid, Goety, Waystones, Farmer's Delight, KubeJS; plus
death/restart/pickup/container/task-cancel/MCP-disconnect. Mark each: **verified /
unsupported / needs-adaptation / failed.** "No crash" ≠ "supported".

## Cross-cutting tests
JUnit units · Forge GameTest (where feasible) · standalone-server integration · MCP
protocol tests · security/concurrency tests. Must cover: no-client-class dedicated smoke;
virtual connection leaks no packets; gravity/fall; auto-pickup; inventory-full no item
loss; native container click; per-companion serial tasks; lease contention; task stops on
death; futures don't hang on server stop; invalid token rejected; body/batch over-limit
rejected; HTTP timeout leaves no ghost task; playerdata survives restart; MCP and API
cannot co-control one companion.

## Final deliverables
Reproducible fork · full source · license/attribution notice · buildable JAR · Forge +
Mohist + modpack-copy test reports · MCP config · API config · security model doc ·
supported/unsupported/needs-adaptation compatibility matrix · production-candidate JAR
(**not** auto-deployed).
