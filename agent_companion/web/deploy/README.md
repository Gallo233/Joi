# Putting the site and Joi on one box

Written for the case this deployment actually has: visitors in mainland China,
no VPN, and a Joi experience that needs a process running somewhere.

## Why not Vercel, Cloudflare Pages or GitHub Pages

None of them reach mainland China reliably. `github.io` is blocked outright;
`pages.dev` has had DNS pollution since 2022; `vercel.app` is in the same
position. Binding a custom domain moves the pollution off the hostname but
does not put an edge node in the country, so the route still leaves the
mainland and the latency shows.

The second reason matters more here: **none of them can run Joi.** The broker
spawns one Core process per visitor and holds a WebSocket open for the length
of a conversation. That is not a serverless function, and no static host will
run it. The site and Joi therefore live on the same machine, which turns out
to be the simpler arrangement anyway — one hostname, one certificate, and no
CORS at all, because the browser sees the same origin for both.

## What this costs and what it needs

| | |
|---|---|
| A domain | ~¥70-90/year. Buy it at Aliyun or Tencent Cloud — the ICP filing later is far easier when the registrar is the same company as the CDN. |
| A Hong Kong VPS | ~¥24-40/month for 2C4G. Hong Kong needs no ICP filing and is directly reachable from the mainland. |
| ICP filing | Free, 2-3 weeks, needs a mainland identity. Not required to launch — start it in parallel and switch to a mainland CDN when it clears. |

Bandwidth is the constraint a cheap Hong Kong plan hits first, not CPU or
memory. A visitor downloads the 4.3MB shell once, and then whatever character
they activate: the Live2D one is ~5MB, the sample VRM is 26MB. `deploy-joi.sh`
takes `JOI_CHARACTER_IDS` for exactly this reason — start narrow.

## The layout

```
Caddy :443  ──┬── /session, /ws/*, /assets/*  ──▶  broker :9080 ──▶ Core per visitor
              └── everything else              ──▶  Next.js :3000
```

Both back ends bind loopback. Caddy is the only thing listening publicly, and
it obtains and renews the certificate itself.

## First install

Run as root unless a step says otherwise.

**1. Point the domain at the box.** An `A` record for `example.com` and one for
`www`. Wait for it to resolve before installing Caddy, or the first
certificate request fails and you wait out a rate limit.

**2. Users and directories.**

```bash
adduser --system --group --home /opt/joi-site joi-site
adduser --system --group --home /opt/joi joi-web
mkdir -p /var/lib/joi-web/{sessions,state} /opt/joi-web-seed
chown -R joi-web:joi-web /var/lib/joi-web /opt/joi-web-seed
```

**3. The site.**

```bash
git clone https://github.com/Gallo233/joi-doorway.git /opt/joi-site
chown -R joi-site:joi-site /opt/joi-site
install -m 0755 -o joi-site -g joi-site \
  /opt/joi/agent_companion/web/deploy/deploy-site.sh /opt/joi-site/deploy-site.sh
cp /opt/joi/agent_companion/web/deploy/joi-site.env.example /etc/joi-site.env
chmod 600 /etc/joi-site.env      # then fill it in
cp /opt/joi/agent_companion/web/deploy/joi-site.service.example \
   /etc/systemd/system/joi-site.service
```

The shell bundle ships inside this repository at `public/joi-shell`, so there
is no second build step and no chance of the page and the shell it embeds
being out of step.

**4. Joi.**

```bash
git clone <your Joi remote> /opt/joi
cd /opt/joi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
install -m 0755 -o joi-web -g joi-web \
  agent_companion/web/deploy/deploy-joi.sh /opt/joi/deploy-joi.sh
cp agent_companion/web/deploy/joi-web.env.example /etc/joi-web.env
cp agent_companion/web/deploy/config.guest.example.yaml /etc/joi-guest.yaml
chmod 600 /etc/joi-web.env       # then fill it in, including the IP hash secret
cp agent_companion/web/deploy/joi-web.service.example \
   /etc/systemd/system/joi-web.service
```

Character packages are not in the repository. Copy the ones you want visitors
to meet to `/opt/joi/character-source/data/agent_companion/characters/packages/`
and name them in `JOI_CHARACTER_IDS`.

**5. Let the deploy scripts restart their own service** — this is the whole
reason a push can deploy without a human:

```bash
cat >/etc/sudoers.d/joi-deploy <<'EOF'
joi-site ALL=(root) NOPASSWD: /bin/systemctl restart joi-site
joi-web  ALL=(root) NOPASSWD: /bin/systemctl restart joi-web
EOF
chmod 440 /etc/sudoers.d/joi-deploy
```

**6. Caddy.**

```bash
cp /opt/joi/agent_companion/web/deploy/Caddyfile.example /etc/caddy/Caddyfile
# replace example.com throughout
systemctl reload caddy
```

**7. Start everything.**

```bash
sudo -u joi-web /opt/joi/deploy-joi.sh
sudo -u joi-site /opt/joi-site/deploy-site.sh
systemctl enable --now joi-web joi-site
```

## Deploying a change

Site changes — which is most of them — go out on a push to `main`. The workflow
in the site repository SSHes in and runs `deploy-site.sh`. Three repository
secrets make that work: `DEPLOY_HOST`, `DEPLOY_USER` (`root`, or any user who
may `sudo -u joi-site`), and `DEPLOY_KEY` (a private key whose public half is
in that user's `authorized_keys`).

A failed build changes nothing: `deploy-site.sh` restarts only after
`next build` succeeds, and then waits for the site to actually answer before
reporting success.

Joi changes are deliberately separate and manual:

```bash
sudo -u joi-web /opt/joi/deploy-joi.sh
```

Conversations already in progress are untouched — each holds its own workspace
and the broker reaps it on TTL — so the new Core applies to whoever arrives
next.

## When the ICP filing clears

Nothing here has to move. Put a mainland CDN in front of the same origin and
point it at the Hong Kong box, or move the site to a mainland host and leave
the broker where it is. The one thing that must change together is
`--allowed-origins` on the broker and `NEXT_PUBLIC_JOI_BROKER_BASE` on the
site: they name the same origin, and a mismatch shows up as a 403 on
`/session` with no other symptom.

## Reading the box

```bash
journalctl -u joi-web -f          # broker and session lifecycle
journalctl -u joi-site -f         # Next.js
ls /var/lib/joi-web/sessions | wc -l    # visitors with a live Core right now
cat /var/lib/joi-web/state/usage-ledger.json   # today's token and voice spend
```

The ledger is the file to watch in the first week. It is what the daily
ceilings are enforced against, and the honest answer to "what is this costing
me".
