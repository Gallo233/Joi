#!/usr/bin/env bash
#
# Bring up the whole anonymous web stack on this machine, the way the VPS will.
#
# Two things make the web experience awkward to try by hand: the seed has to be
# built from installed character packages before a broker can serve anything,
# and the broker refuses any origin it was not told about -- correctly, but it
# means a forgotten flag looks like a broken page. This does both, from the one
# place that knows where they live.
#
#   agent_companion/web/run_local_demo.sh                 # site on :3000
#   JOI_SITE_ORIGIN=http://localhost:4000 …/run_local_demo.sh
#
# Ctrl-C stops the broker and every Core it started.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${JOI_PYTHON:-$REPO/.venv/bin/python}"
STATE_ROOT="${JOI_WEB_STATE_ROOT:-${TMPDIR:-/tmp}/joi-web-demo}"
SITE_ORIGIN="${JOI_SITE_ORIGIN:-http://localhost:3000}"
BROKER_PORT="${JOI_BROKER_PORT:-9080}"

# The installed app's data home, which is where real character packages live.
# A source checkout's own workspace only ever has the bootstrap character, and
# that one has no artwork -- a guest would meet an empty stage.
CHARACTER_SOURCE="${JOI_CHARACTER_SOURCE:-$HOME/Library/Application Support/com.gallo233.joi}"
GUEST_CONFIG="${JOI_GUEST_CONFIG:-$REPO/agent_companion/web/deploy/config.guest.example.yaml}"

if [ ! -x "$PYTHON" ]; then
  echo "no interpreter at $PYTHON; set JOI_PYTHON" >&2
  exit 1
fi
if [ ! -d "$CHARACTER_SOURCE" ]; then
  echo "no character packages at $CHARACTER_SOURCE; set JOI_CHARACTER_SOURCE" >&2
  exit 1
fi

mkdir -p "$STATE_ROOT/sessions" "$STATE_ROOT/state"

# The shipped guest profile reads its providers from /etc/joi-web.env, which no
# laptop has. Inherit them from the desktop config that already works, so the
# local demo talks to the same model you do instead of reporting "BYOK 请求没有
# 成功" at every message. Keys are not copied -- they stay as ${...} references
# and resolve from the environment or the system keyring.
DESKTOP_CONFIG="${JOI_DESKTOP_CONFIG:-$REPO/config.yaml}"
RESOLVED_CONFIG="$STATE_ROOT/guest.config.yaml"
echo "==> composing guest profile"
"$PYTHON" -m agent_companion.web.local_guest_config \
  --source "$DESKTOP_CONFIG" \
  --template "$GUEST_CONFIG" \
  --output "$RESOLVED_CONFIG"
GUEST_CONFIG="$RESOLVED_CONFIG"

# Keys kept in secrets.yaml have no keyring path; hand them over as environment
# so each Core inherits them without any of them touching a generated file.
eval "$("$PYTHON" -m agent_companion.web.local_guest_config \
  --source "$DESKTOP_CONFIG" --template "$GUEST_CONFIG" --output /dev/null --print-env)"

echo "==> building guest seed from $CHARACTER_SOURCE"
"$PYTHON" -m agent_companion.web.prepare_seed \
  --output "$STATE_ROOT/seed" \
  --guest-config "$GUEST_CONFIG" \
  --character-source "$CHARACTER_SOURCE" \
  --replace >/dev/null

# Hashes visitor IPs so the daily counter never stores an address. Regenerated
# per run here; the deployed unit keeps a fixed one so counts survive restarts.
export JOI_WEB_IP_HASH_SECRET="${JOI_WEB_IP_HASH_SECRET:-$(head -c 48 /dev/urandom | base64)}"

echo "==> broker on http://127.0.0.1:$BROKER_PORT, serving $SITE_ORIGIN"
echo "    point the site at it with NEXT_PUBLIC_JOI_BROKER_BASE=http://127.0.0.1:$BROKER_PORT"
exec "$PYTHON" -m agent_companion.web.broker \
  --host 127.0.0.1 --port "$BROKER_PORT" \
  --public-base "http://127.0.0.1:$BROKER_PORT" \
  --allowed-origins "$SITE_ORIGIN" \
  --seed-workspace "$STATE_ROOT/seed" \
  --guest-config-template "$GUEST_CONFIG" \
  --sessions-root "$STATE_ROOT/sessions" \
  --state-root "$STATE_ROOT/state" \
  --core-python "$PYTHON" \
  --max-concurrent 20 \
  --ip-daily-sessions 50
