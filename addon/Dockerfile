ARG BUILD_FROM=python:3.13-alpine
FROM ${BUILD_FROM}

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
