# Joi Web integration

This directory exposes the existing Joi Shell through two iframes connected to
one isolated Core session. It does not reuse the older `web_widget` prototype.

## Build the website assets

```bash
cd agent_companion/shell
npm ci
npm run build:web
```

Publish `shell/dist/` at `/joi-shell/`. Copy `web/static/joi-embed.js` and
`joi-embed.css` into the personal site, then adapt `joi-example.html`. Replace
the example broker domain before publishing.

## Prepare a clean VPS seed

Do not point the broker at a personal Joi workspace. It can contain private
memory, logs, screenshots and keys. Instead, let the seed builder read only the
JoiDebug character-package directory and combine it with the checked-in guest
profile:

```bash
.venv/bin/python -m agent_companion.web.prepare_seed \
  --guest-config agent_companion/web/deploy/config.guest.example.yaml \
  --character-source /path/to/JoiDebug \
  --character-ids momose-hiyori,avatarsample-a,test-mmd-miku,test-tachie-catgirl \
  --output /opt/joi-web-seed
```

The resulting seed contains `config.yaml` and exactly these four JoiDebug
packages: 桃濑日和 Hiyori (Live2D), AvatarSample_A (VRM plus every authored
VRMA clip), MMD 测试（Miku）(MMD plus VMD), and 立绘测试（Cat girl）. Package trees
retain all declarative character content byte-for-byte, including models,
motions, expressions, voice references, licence/readme files and manifest
metadata. `builtin-hikari` (星野澪) and
`official-seed-san-vrm-test` (Seed-san) are deliberately absent and a seed
marker prevents Core from bootstrapping the built-in role back into guest
sessions.

Only `characters/packages` is read from JoiDebug. Character runtime state,
memory databases, chat, logs and settings are never copied. Provider keys
remain environment references. The builder validates package structure but
does not silently rewrite or prune a package; confirm that every included
asset's declared licence permits how the public site will serve it before
deployment. Unreferenced development environments and executable helpers are
not character assets and remain forbidden in Core packages; if JoiDebug has
such files (for example a transcription `.venv` or `.py` helper), the builder
records their package-relative path and SHA-256 in `web-seed-report.json`
instead of silently dropping them. It never removes VRMA, VMD, model, image,
audio, expression, readme or manifest content.

## Run locally behind Caddy

Set a random `JOI_WEB_IP_HASH_SECRET` of at least 32 bytes plus the provider
variables in `deploy/joi-web.env.example`, then run:

```bash
.venv/bin/python -m agent_companion.web.broker \
  --public-base https://joi.example.com \
  --allowed-origins https://www.example.com,https://example.com \
  --seed-workspace /opt/joi-web-seed \
  --sessions-root /var/lib/joi-web/sessions \
  --state-root /var/lib/joi-web/state
```

The broker and every Core bind only to loopback. Caddy owns TLS and forwards
the public path set to the broker; the broker resolves the session to its WS or
asset port. Caddy cannot inspect encrypted WebSocket JSON-RPC frames, so Core
enforces the method allowlist after token authentication and before dispatch.

Defaults are 150 concurrent sessions, five sessions per hashed IP per UTC day,
30,000 tokens per session, 1,500,000 tokens globally per UTC day, 180 seconds
per realtime call, 600 realtime seconds per visitor, and 10,800 realtime
seconds globally per UTC day. All are broker CLI options.

Use the example Caddy and systemd files as templates. Update both domains,
paths and provider choices; validate with `caddy validate` and
`systemd-analyze verify` on the target Linux host before enabling the unit.
