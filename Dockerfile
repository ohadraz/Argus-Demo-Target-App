FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --frozen --no-dev

EXPOSE 8000
EXPOSE 8443

CMD ["uv", "run", "python", "-m", "target_app.serve"]
