# Minecraft Realtime P0–P2 contract

Status: implemented behind Core RPC; Shell voice orchestration, real-server
smoke and debug packaging remain P3.

## Frozen boundary

The only executable input is a final, structured `GameIntent` from `voice` or
`text`. Core supports exactly:

`observe`, `inventory`, `follow_player`, `come_to_player`, `collect`, `mine`,
`craft`, `eat`, `place_blueprint`, and `deposit`.

Each action has a closed schema. Unknown/extra fields, arbitrary JavaScript,
commands or source strings fail closed. `place_blueprint` uses relative offsets
only, rejects duplicate offsets and dangerous blocks, and is bounded to 128
blocks, 16 blocks per axis and a 4096-block bounding volume.

The user-confirmed scope names a server, world, dimensions, radius, action and
block-change budgets, allowed blocks/players, and build/container capability.
Selecting companion/delegate mode does not itself grant world access.
The preview returns a five-minute, single-use approval ID bound to the exact
mode, scope and budget digest; a changed or replayed confirmation is rejected.

## Runtime path

1. `game.adapter.session.start` validates and confirms scope, creates the Core
   capability session/grant, then starts one persistent bridge process.
2. `game.adapter.goal.submit` requires `session_id`, `goal_id`, `final`,
   `source`, and strict `intent`.
3. Core checks scope, calls the shared `action_allowed` gate and reserves action
   and block budgets before bridge I/O.
4. The v2 bridge validates protocol/session/message/goal IDs and directionally
   monotonic sequence numbers. Duplicate message IDs replay only a cached
   response; conflicting reuse locks the transport.
5. Core is the authority for receipts. `completed` requires after-state;
   otherwise the result is `partial`, `unverified`, or `failed`.

`game.adapter.goal.pause/resume/cancel` require both IDs. A missing ACK forces
process termination. Disconnects and uncertain effects enter
`recovery_required`; no goal is restarted or replayed. Exact coordinates,
inventory checkpoints, child stderr and bridge IDs are not returned as user or
voice summaries.

## P0–P2 verification

```bash
.venv/bin/python -m unittest tests.test_minecraft_v2
node --check agent_companion/adapters/minecraft-bridge/index.js
```

The suite covers strict primitive schemas, partial transcript zero-action,
scope/permission/budget zero-mutation paths, blueprint limits, receipts,
duplicate/replay protection, wrong session/sequence gaps, cancel ACK,
disconnect-with-uncertain-effect, persistent multi-goal sessions and a secret
environment canary.

The connection identity is explicit operator configuration:
`JOI_MINECRAFT_SERVER_ID` and `JOI_MINECRAFT_WORLD` must exactly match the
confirmed scope before the bridge connects. The first spawned position becomes
the session's fixed spatial origin. Every target and path step stays within the
confirmed radius, and each dimension-bearing intent must match the bot's live
dimension. `collect` additionally verifies the requested item reached
inventory; `mine` verifies the target block changed.

## Deferred P3

- Shell journey and live state rendering (`connecting`, `ready`, `acting`,
  `paused`, `cancelled`, `disconnected`, `recovery_required`).
- Realtime voice-to-intent orchestration and interruption UX.
- Real Minecraft server smoke for all primitives and construction quality.
- Mineflayer dependency packaging, debug app rebuild and install/update flow.
