#!/usr/bin/env bash
#
# Update the Joi Core the sessions are cut from.
#
# Separate from the site deploy because it is a different cadence and a
# different blast radius: a site push changes a paragraph, this one changes the
# runtime every visitor gets a copy of. Running sessions are left alone -- they
# hold their own workspace, and the broker reaps them on TTL -- so a deploy is
# invisible to anyone mid-conversation and applies to whoever arrives next.
#
# Install a copy of this file OUTSIDE the checkout and run that copy:
#
#     install -m 755 agent_companion/web/deploy/deploy-joi.sh /usr/local/sbin/joi-deploy
#     joi-deploy
#
# Not the copy in the tree. This script resets the tree it lives in, and bash
# reads a script incrementally by byte offset -- so rewriting the file mid-run
# makes the shell resume at an offset that now means something else.
#
# What this box runs, rather than what a generic install runs, belongs in the
# environment file below and not in an edited copy of this script. An edited
# copy is a fork that silently stops tracking the original; the last one on this
# box had drifted a character list, and nothing said so.

set -euo pipefail

# Per-box settings: which branch this box runs, which characters it serves.
# Sourced before anything is decided, and written with `${VAR:-default}` so a
# one-off `JOI_BRANCH=... joi-deploy` on the command line still wins.
DEPLOY_ENV="${JOI_DEPLOY_ENV:-/etc/joi-deploy.env}"
if [ -f "$DEPLOY_ENV" ]; then
  # shellcheck source=/dev/null
  . "$DEPLOY_ENV"
fi

JOI_DIR="${JOI_DIR:-/opt/joi}"
BRANCH="${JOI_BRANCH:-main}"
SEED_DIR="${JOI_SEED_DIR:-/opt/joi-web-seed}"
GUEST_CONFIG="${JOI_GUEST_CONFIG:-/etc/joi-guest.yaml}"
BROKER_UNIT="${JOI_BROKER_UNIT:-/etc/systemd/system/joi-web.service}"
# Which characters a visitor can meet. Every one of them is copied into the
# seed and served from this box, so this list is also the bandwidth bill: the
# sample VRM alone is 26MB per visitor who activates it.
CHARACTER_IDS="${JOI_CHARACTER_IDS:-momose-hiyori,test-tachie-catgirl}"

# An installed copy cannot update itself -- it is the file currently executing --
# so it says when the tree has moved past it. Silence here is the whole failure
# mode of copying a script: it keeps working, one version behind, indefinitely.
report_if_stale() {
  local tracked="$JOI_DIR/agent_companion/web/deploy/deploy-joi.sh"
  [ -f "$tracked" ] || return 0
  cmp -s "$0" "$tracked" && return 0
  echo "note: $0 differs from $tracked. Refresh it with:"
  echo "      install -m 755 $tracked $0"
}

cd "$JOI_DIR"

echo "==> fetching $BRANCH"
git fetch --quiet origin "$BRANCH"

# `git reset --hard` is the point of no return, and BRANCH is a variable with a
# default. A branch from before Joi Web existed resets cleanly and leaves a Core
# that does not understand one argument the broker passes it -- the site keeps
# rendering, and nobody finds out until a visitor cannot open a session. So the
# target answers for itself first, while the checkout is still untouched.
#
# The checker is read out of the target rather than the working tree, which is
# what lets this bootstrap onto a box running an older deploy -- and makes a
# target too old to carry one fail at the first step, which is the same answer
# by a shorter route.
echo "==> checking $BRANCH can run this deployment"
CHECKER="$(mktemp)"
trap 'rm -f "$CHECKER"' EXIT
if ! git show "origin/$BRANCH:agent_companion/web/deploy/verify_target.py" > "$CHECKER" 2>/dev/null; then
  echo "!! origin/$BRANCH does not carry agent_companion/web/deploy/verify_target.py," >&2
  echo "   which every branch that can run Joi Web has. This is almost certainly the" >&2
  echo "   wrong branch. Nothing has been changed; set JOI_BRANCH and run again." >&2
  exit 1
fi
.venv/bin/python "$CHECKER" --target "origin/$BRANCH" --repo "$JOI_DIR" --unit "$BROKER_UNIT"

git reset --quiet --hard "origin/$BRANCH"
echo "    at $(git rev-parse --short HEAD)"

echo "==> python dependencies"
.venv/bin/python -m pip install --quiet --upgrade -r requirements.txt

echo "==> rebuilding guest seed ($CHARACTER_IDS)"
.venv/bin/python -m agent_companion.web.prepare_seed \
  --output "$SEED_DIR.next" \
  --guest-config "$GUEST_CONFIG" \
  --character-source "$JOI_DIR/character-source" \
  --character-ids "$CHARACTER_IDS" \
  --replace >/dev/null

# Swap rather than rebuild in place: sessions hardlink out of the seed, and a
# seed that is half-written while one is being created would hand that visitor
# a character with no textures.
rm -rf "$SEED_DIR.old"
[ -d "$SEED_DIR" ] && mv "$SEED_DIR" "$SEED_DIR.old"
mv "$SEED_DIR.next" "$SEED_DIR"
rm -rf "$SEED_DIR.old"

echo "==> restarting broker"
sudo systemctl restart joi-web

for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null --max-time 2 -X POST http://127.0.0.1:9080/session \
      -H 'Origin: http://127.0.0.1' 2>/dev/null; then
    break
  fi
  # A 403 means it is up and correctly refusing an unknown origin, which is
  # the healthiest possible answer to that probe.
  if curl -s -o /dev/null -w '%{http_code}' --max-time 2 -X POST http://127.0.0.1:9080/session | grep -q '^403$'; then
    echo "==> broker up"
    report_if_stale
    exit 0
  fi
  sleep 1
done

echo "!! broker did not answer; check: journalctl -u joi-web -n 50" >&2
exit 1
