# Argus-Demo-Target-App

This is a **test/demo fixture for [Argus](https://github.com/ohadraz/Argus)** - an autonomous incident-response agent. It has no purpose or life of its own outside that project: it exists so Argus has a real, small, runnable app to watch, investigate, and mitigate against instead of synthetic data.

This repository carries no independent design spec or `openspec/` change tracking - all of that lives in the Argus repository, since this fixture's rationale only makes sense in that context. See Argus's `openspec/changes/target-service-scaffold/` for why this repo exists and what it's for.

## Running locally

```bash
uv sync
uv run uvicorn target_app.app:app --reload
```

Or via Argus's `docker-compose.yml`, which brings this service up alongside Argus's own stack (requires this repo checked out as a sibling directory to Argus).
