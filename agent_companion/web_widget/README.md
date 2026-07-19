# Joi Floating Assistant Widget

Embeddable website widget for Joi. It renders Joi as a draggable floating desk-pet assistant with mouse-following head movement and an optional WebSocket connection to Joi Core.

## Use

```html
<script src="/joi-floating-assistant.js" defer></script>
<joi-floating-assistant core-url="ws://127.0.0.1:8765"></joi-floating-assistant>
```

Optional attributes:

- `core-url`: Joi Core WebSocket JSON-RPC endpoint.
- `asset-base`: folder URL containing `joi-body.png`, `joi-front-head.png`, and expression assets.
- `start-open`: open the chat panel on load.

## Run Joi Core

```bash
cd /path/to/Joi
.venv/bin/python -m agent_companion.core.main --serve --workspace . --host 127.0.0.1 --port 8765
```

Do not expose Joi Core directly on the public internet. Put a server-side auth and method whitelist proxy in front of it before using it on a public site.

## Rebuild Assets

```bash
/Users/liujialuo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/build_assets.py --source "/Users/liujialuo/Downloads/已生成图像 4 (1).png"
```
