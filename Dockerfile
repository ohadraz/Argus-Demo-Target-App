FROM python:3.14-slim

# So the shop can run on the stack's clock rather than the wall's - turned on
# by the compose file, never here. See `sim-clock` there.
RUN apt-get update \
    && apt-get install -y --no-install-recommends faketime \
    && rm -rf /var/lib/apt/lists/* \
    && find /usr/lib -path '*/faketime/libfaketime.so.1' -exec ln -s {} /usr/local/lib/libfaketime.so.1 \;

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --frozen --no-dev

# The configuration the shop is deployed with, which it reads at startup. After
# the sync rather than beside `src/`, so that changing a value does not
# re-resolve every dependency.
COPY deploy/ deploy/

EXPOSE 8000
EXPOSE 8443

CMD ["uv", "run", "python", "-m", "target_app.serve"]
