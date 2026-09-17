# agent (คน 3)

Finite, budgeted orchestration of one travel-safety run. The graph is a fixed LangGraph `StateGraph` (no free-form
tool loop): every node calls at most one allowlisted tool, every tool call is contract-validated on both sides, and the
run ends in a terminal `RunStatus` (`COMPLETED | PARTIAL | NEEDS_INPUT | FAILED | CANCELLED`).

```text
AgentRunRequest{travel_request, conversation_id?, resume_from_request_id?}
  -> validate_input          (schema + injection heuristics on question -> limitation flag; never a rule change)
  -> classify_intent         (rules first: EMERGENCY > hint > follow-up keywords > CHECK_SAFETY > PLAN_TRIP)
  -> check_required_fields   (unconfirmed/missing origin, destination, departure -> NEEDS_INPUT, no guessing)
  -> [EMERGENCY]             -> COMPLETED with emergency.shortcut, zero provider calls
  -> [follow-up, informational, snapshot < FOLLOWUP_REUSE_MAX_AGE_SECONDS] -> build_evidence (reuse snapshot)
  -> fetch_external_data     external_data.query_context@1        (FETCHING_EXTERNAL_DATA)
  -> integrate_data          data_integration.create_snapshot@1   (INTEGRATING_DATA)
  -> build_evidence          risk_knowledge.build_evidence_package@1 (ASSESSING_RISK -> RETRIEVING_GUIDANCE -> EVALUATING_ROUTES)
  -> make_decision           decision_engine.create_decision@1    (MAKING_DECISION -> EXPLAINING)
  -> format_recommendation   recommendation.create_recommendation@1 (FORMATTING_RESPONSE)
```

Consistency gates between nodes: snapshot ↔ request/trip revision, evidence package ↔ snapshot, decision ↔ request +
snapshot, recommendation ↔ request + locked `action_code` + `decision_id`. Any mismatch fails the run with
`POLICY_VALIDATION_FAILED`; nothing is bypassed or retried past a 4xx.

## Budgets (settings → `.env`)

| Setting | Default | Enforced by |
| --- | --- | --- |
| `MAX_AGENT_STEPS` | 12 | `Budget.step()` on every node entry |
| `MAX_TOOL_CALLS` | 10 | `ToolRunner.call` |
| `AGENT_TOTAL_TIMEOUT_SECONDS` | 45 | `asyncio.wait_for` around the graph |
| `AGENT_TOOL_TIMEOUT_SECONDS` | 12 (per-tool caps in registry) | `ResilientClient` deadline |
| `MAX_LLM_CALLS` / `MAX_ESTIMATED_COST_USD` | 2 / 0.05 | budget counters (LLM intent classification is optional) |

## Progress stream (SSE contract for คน 2)

Events are `RunProgressEvent`s appended to the Redis stream `sta:{env}:run:{request_id}:events` and mirrored in
memory: `run.accepted`, `run.progress` (one per `RunStage`, monotonic `event_id`), `run.degraded`, `run.needs_input`,
`run.completed`, `run.failed` (`retryable` flag). Events carry stage/status/message keys only — no coordinates,
prompts, tokens or provider payloads. `GET /internal/v1/runs/{id}/events` returns the same list for polling.

## API (`/internal/v1`, service token)

| Endpoint | Behaviour |
| --- | --- |
| `POST runs` → 202 `RunRef` | 409 if the `request_id` already exists |
| `GET runs/{id}` | `RunState` (status, ids, versions, degraded, limitations, budgets) |
| `GET runs/{id}/events` | progress events |
| `POST runs/{id}/resume` → 202 | requires a **new** `request_id` (422), same trip (403), source status in `NEEDS_INPUT/COMPLETED/PARTIAL` (409) |
| `POST runs/{id}/cancel` | cooperative cancel; the running task stops at the next node boundary |
| `GET tools` | the allowlist with stages, timeouts, attempts |

## Persistence

`agent.runs` (compact state: ids, snapshot for follow-up reuse, versions, budgets — no provider payloads) and
`agent.tool_calls` (tool, input sha256, status, duration, error code). Graph checkpoints use LangGraph
`AsyncPostgresSaver` (`MemorySaver` under `APP_ENV=test`). Run state also lives in Redis (`sta:{env}:run:{id}:status`,
TTL `RUN_STATE_TTL_SECONDS`) so the API can answer polls without touching PostgreSQL.

## Tests

`uv run pytest -q` → 12 passed: full chain with stage order + event monotonicity + audit hashes, needs-input → resume,
emergency shortcut, provider outage (stable code, no bypass), malformed downstream payload, cancel, follow-up reuse vs
forced refresh, tool budget, injection cannot change the locked action, intent rules, finite graph, tool allowlist.
Downstream services are stubbed with respx from the real contracts so ids chain exactly like production.
