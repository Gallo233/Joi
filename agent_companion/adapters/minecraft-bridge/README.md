# Joi Minecraft bridge

This adapter runs outside Joi's main process and accepts one JSON-line request
using `joi.game_adapter.v1`. It does not contain credentials. Configure the
server with environment variables (`JOI_MINECRAFT_HOST`, `PORT`, `USERNAME`,
and optionally `AUTH`) before enabling it.

Dependencies are intentionally not installed by Joi without an explicit
adapter-install approval. From this directory, review `package.json`, then run
`npm install`. Joi will detect the bridge on its next adapter refresh.
