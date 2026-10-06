# Postgres on the stack's clock. The official image plus libfaketime, which a
# service turns on with `LD_PRELOAD` and points at the clock writer's spec file -
# see `sim-clock` in docker-compose.yml. Nothing is faked until a service asks.
FROM postgres:16

# Linked to one path whatever the architecture - Debian files the library under
# the machine's triplet, and a preload of a path that is not there is ignored.
RUN apt-get update \
    && apt-get install -y --no-install-recommends faketime \
    && rm -rf /var/lib/apt/lists/* \
    && find /usr/lib -path '*/faketime/libfaketime.so.1' -exec ln -s {} /usr/local/lib/libfaketime.so.1 \;
