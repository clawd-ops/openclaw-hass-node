# Under HA Supervisor BUILD_FROM is supplied by build.yaml as one of
# ghcr.io/home-assistant/<arch>-base-python:3.13-alpine3.20. The default
# below matches that family rather than a bare python:3.13-alpine, which
# Supervisor would silently reject (the resulting container has no `pip`
# on PATH).
#
# The HA base-python image is REQUIRED, not just preferred: run.sh uses
# the `#!/usr/bin/with-contenv sh` shebang to pick up Supervisor's
# injected env (notably SUPERVISOR_TOKEN — see Issue #109). Bare
# python:3.13-alpine does not ship s6-overlay / with-contenv, so the
# addon won't start there. Standalone-Docker is not a supported build
# path while the project is in alpha.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.20
FROM ${BUILD_FROM}

# Build context is the app/ directory (Supervisor convention).
COPY node /opt/openclaw-node
COPY run.sh /run.sh

# Runtime dependencies are intentionally minimal for the first installable
# slice. Build deps are removed after installing Python wheels.
RUN apk add --no-cache --virtual .build-deps \
      gcc \
      libffi-dev \
      musl-dev \
      openssl-dev \
    && pip install --no-cache-dir /opt/openclaw-node \
    && apk del .build-deps \
    && chmod +x /run.sh \
    && mkdir -p /data/openclaw

CMD ["/run.sh"]
