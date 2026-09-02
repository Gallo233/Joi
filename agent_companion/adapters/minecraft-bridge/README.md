# Joi Minecraft bridge

This adapter runs outside Joi's main process as one persistent process per
Minecraft session. It exchanges strict JSON Lines envelopes using
`joi.game_adapter` version `2`; arbitrary commands, code and natural-language
goals are not accepted. Configure the server with `JOI_MINECRAFT_HOST`,
`JOI_MINECRAFT_PORT`, `JOI_MINECRAFT_USERNAME`, and optionally
`JOI_MINECRAFT_AUTH` before enabling it.

Set `JOI_MINECRAFT_SERVER_ID` and `JOI_MINECRAFT_WORLD` to stable operator
labels for that connection. They are required and must match the exact scope
the user previewed and approved.

Supported Minecraft versions are mineflayer's tested list plus the releases named
in `compatibleVersions` in `index.js`, which are those this bridge carries a
verified compatibility shim for (currently `26.1`). Anything newer fails closed
with `minecraft_version_unsupported` rather than half-connecting. Adding a
release means adding the shim for whatever packets it reshaped, then verifying a
real join — the version gate alone is not evidence that a release works.

Core is the permission, scope, budget and receipt authority. The bridge only
executes the ten reviewed primitives and returns observation evidence. Pause,
resume and cancel are scoped to `session_id` plus `goal_id` and require an ACK;
a disconnect never causes an automatic action replay. Pathfinding is
non-destructive by default (`canDig=false`, no 1x1 towers or scaffolding).

Dependencies are intentionally not installed by Joi without an explicit
adapter-install approval. Release engineering uses `npm ci` from the committed
lockfile and `npm run build`. The reviewed runtime is `dist/index.js` plus the
pinned `dist/vendor/minecraft-data` and the platform Node executable used for
the reviewed build; raw development `node_modules` is excluded from the Core
sidecar and app bundle. Joi will detect that built runtime on its next adapter
refresh.

For a custom bridge executable, set both `JOI_MINECRAFT_BRIDGE_COMMAND` and its
reviewed `JOI_MINECRAFT_BRIDGE_SHA256`. Joi rejects an unpinned executable and
passes only the explicit Minecraft environment allowlist to the child.
