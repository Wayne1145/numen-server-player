# Notice and attribution

`numen-server-player` is an unofficial server-side derivative of two Numen repositories:

| Component | Upstream | Branch | Pinned base |
|---|---|---|---|
| Engine / public API | https://github.com/Dwinovo/numen-api | `1.20.1` | `87fd80a8531a2866c19f41048623b5d4b2197d52` |
| Tools / tasks / pathing | https://github.com/Dwinovo/minecraft-numen | `1.20.1` | `c28ad1ef4b21af8e873a8cf0ec9ac2b10885b4c6` |

Server-side fork tips represented by this public snapshot:

- `numen-api`: `4a864fa88c80f40becb192e10ba7499e6c898c72`
- `minecraft-numen`: `eeb9b710ef79b9c8f32058246645133faa93697b`

The project is not an official Numen release. The names “Numen” and “言出法随” and the original branding remain reserved by the upstream copyright holder.

## License scopes

- Core source code and derivative server implementation: **LGPL-3.0-only** — see `LICENSE`.
- Public integration API under `com.dwinovo.numen.api`: **MIT** — see `LICENSE-API`.
- Original Numen visual/audio assets and branding: **All Rights Reserved** — see `LICENSE-ASSETS`.

The public snapshot intentionally removes the upstream ARR image/audio asset files. It retains `LICENSE-ASSETS` so downstream users understand that removing or replacing those assets does not relicense them.

## Fork changes

The server fork adds or changes, among other things:

- server-owned `ServerPlayer` companion lifecycle;
- dedicated-server-safe tick dispatch and health watchdog;
- unified `ServerNumenActuator`;
- principals, capability policy, rate limits and audit events;
- control Lease with TTL and fencing tokens;
- loopback-first JSON-RPC MCP endpoint and stdio adapter;
- optional OpenAI-compatible API Agent using the same actuator and Lease;
- task cancellation and shutdown/death cleanup;
- Forge/Mohist and modpack-copy acceptance tooling.

The corresponding source for the distributed implementation is this repository at the same commit as the build. No Minecraft server binaries, Mohist binaries, modpacks, worlds or third-party mod JARs are distributed here.
