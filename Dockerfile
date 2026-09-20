FROM python:3.14-slim

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
