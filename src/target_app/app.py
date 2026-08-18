from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

SCENARIOS: dict[str, list[str]] = {
    "feature-flag-toggle": [
        "INFO target-service: feature flag 'checkout-v2' is off, request succeeded",
        "INFO target-service: feature flag 'checkout-v2' is off, request succeeded",
        "WARN target-service: feature flag 'checkout-v2' toggled from 'off' to 'on'",
        "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate elevated",
        "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate elevated",
        "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate at 41% over the last minute",
    ],
    "bad-deployment": [
        "INFO target-service: deploy started, version 1.4.2 -> 1.4.3",
        "INFO target-service: deploy completed, version 1.4.3 live",
        "WARN target-service: p95 latency climbing since deploy of version 1.4.3",
        "WARN target-service: p95 latency at 1800ms, up from a 220ms baseline",
        "ERROR target-service: request timeout after 5000ms, version 1.4.3",
        "ERROR target-service: request timeout after 5000ms, version 1.4.3",
    ],
}

# In-memory only: a restart clears this back to None. The scenario content
# above is code, not state, so it survives restarts unaffected.
_active_scenario: str | None = None


class SeedRequest(BaseModel):
    scenario_id: str


class ScenarioStatus(BaseModel):
    active_scenario: str | None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/scenario/seed", response_model=ScenarioStatus)
def seed_scenario(body: SeedRequest) -> ScenarioStatus:
    global _active_scenario
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"unknown scenario id: {body.scenario_id}")
    _active_scenario = body.scenario_id
    return ScenarioStatus(active_scenario=_active_scenario)


@app.post("/scenario/reset", response_model=ScenarioStatus)
def reset_scenario() -> ScenarioStatus:
    global _active_scenario
    _active_scenario = None
    return ScenarioStatus(active_scenario=_active_scenario)


@app.get("/scenario/status", response_model=ScenarioStatus)
def scenario_status() -> ScenarioStatus:
    return ScenarioStatus(active_scenario=_active_scenario)


@app.get("/logs", response_model=list[str])
def logs() -> list[str]:
    if _active_scenario is None:
        return []
    return SCENARIOS[_active_scenario]
