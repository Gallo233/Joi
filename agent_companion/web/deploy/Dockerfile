# The Joi web stack as one image: broker plus the Cores it spawns.
#
# Not a base for the desktop app. `requirements.txt` carries PySide6 and the
# macOS bridges the shell needs and a server never loads, so this installs a
# web-only subset -- the difference is roughly 400MB of image nobody executes.
#
# The Cores the broker starts are child processes of this container, and their
# ports (9100+) are loopback inside it. Only the broker's own port is published.
#
# Build and run:
#   docker build -f agent_companion/web/deploy/Dockerfile -t joi-web .
#   docker compose -f agent_companion/web/deploy/compose.yaml up -d

FROM python:3.12-slim AS runtime

# tini reaps the Core processes the broker spawns. Without an init, a container
# that has restarted a few sessions accumulates zombies until it hits its PID
# limit and stops being able to start new ones.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/joi

COPY agent_companion/web/deploy/requirements-web.txt ./requirements-web.txt
RUN pip install --no-cache-dir -r requirements-web.txt

# Source last, so a code change does not reinstall the dependency layer.
COPY agent_companion ./agent_companion

# Unprivileged, and owning nothing it writes to: the seed is mounted read-only
# and sessions live on a volume.
RUN useradd --system --uid 10001 --home /opt/joi joi \
 && mkdir -p /var/lib/joi-web/sessions /var/lib/joi-web/state \
 && chown -R joi:joi /var/lib/joi-web
USER joi

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 9080

# The broker refuses any origin it was not told about, so a container with no
# arguments is deliberately useless -- see compose.yaml for the real command.
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "agent_companion.web.broker"]
CMD ["--help"]
