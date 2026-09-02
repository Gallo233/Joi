#!/usr/bin/env bash
#
# Put the current main branch of the site on the box.
#
# The shell bundle is not built here: `public/joi-shell` is committed, so a
# `git pull` already carries it. That keeps the deploy to one language and one
# toolchain, and means a site change and the shell it embeds always ship as
# one commit rather than two things that can be out of step.
#
# Idempotent and safe to run from a webhook, a GitHub Action, or by hand:
#
#     sudo -u joi-site /opt/joi-site/deploy-site.sh
#
# A failed build leaves the running site untouched: nothing restarts until
# `next build` has succeeded.

set -euo pipefail

SITE_DIR="${JOI_SITE_DIR:-/opt/joi-site}"
BRANCH="${JOI_SITE_BRANCH:-main}"
SITE_ENV="${JOI_SITE_ENV:-/etc/joi-site.env}"

# `NEXT_PUBLIC_*` is inlined at build time, not read at runtime, so the
# systemd unit's EnvironmentFile is too late for it: a build without these
# produces a bundle whose broker address is the empty string, and the page
# renders its offline notice against a broker that is running perfectly well.
# Nothing errors, which is why it took a screenshot to notice.
if [ -r "$SITE_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SITE_ENV"
  set +a
else
  echo "!! cannot read $SITE_ENV -- the build will inline empty NEXT_PUBLIC_* values" >&2
  exit 1
fi

cd "$SITE_DIR"

echo "==> fetching $BRANCH"
git fetch --quiet origin "$BRANCH"
before="$(git rev-parse HEAD)"
git reset --quiet --hard "origin/$BRANCH"
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "    already at $after"
else
  echo "    $before -> $after"
fi

# `npm ci` only when the lockfile moved: it deletes node_modules and reinstalls
# from scratch, which is a minute of a small box's life for nothing when the
# change was a paragraph of copy.
if [ "$before" = "$after" ] && [ -d node_modules ]; then
  echo "==> dependencies unchanged"
elif git diff --quiet "$before" "$after" -- pnpm-lock.yaml package.json 2>/dev/null && [ -d node_modules ]; then
  echo "==> dependencies unchanged"
else
  echo "==> installing dependencies"
  pnpm install --frozen-lockfile
fi

echo "==> building"
pnpm run build

echo "==> restarting"
sudo systemctl restart joi-site

# Wait for it to answer rather than declaring success on `systemctl` alone: a
# unit that starts and immediately crashes still exits 0 here.
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:3000/; then
    echo "==> live at $after"
    exit 0
  fi
  sleep 1
done

echo "!! site did not answer within 30s; check: journalctl -u joi-site -n 50" >&2
exit 1
