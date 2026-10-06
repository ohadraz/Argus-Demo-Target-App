# The flag provider on the stack's clock. The pinned image plus libfaketime,
# which the service turns on with `LD_PRELOAD` - see `sim-clock` in
# docker-compose.yml. Alpine, so the library comes from apk.
FROM unleashorg/unleash-server:8.1.0

USER root
# The thread-safe build, because Node is threaded, at the path every faked
# service in the stack preloads.
RUN apk add --no-cache libfaketime \
    && ln -s /usr/lib/faketime/libfaketimeMT.so.1 /usr/local/lib/libfaketime.so.1
USER node
