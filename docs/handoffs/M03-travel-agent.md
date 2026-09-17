# [M03] Travel Agent Orchestration — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M03 Agent Orchestration — คน 3 |
| Branch | `feat/03-agent` → main (`[M03] Add finite LangGraph orchestration with budgets, progress stream and follow-up handling`) |
| Base SHA | `154118b` (M08 merge) |
| Date | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 2 (run/SSE contract), คน 4–8 (tool contracts), Team Lead (budgets) |
| Versions | graph `1.0.0+<checksum16>` (checksum of node/edge list), prompt 1.0.0, contract 1.0.0 |

## 2. Executive summary

- Orchestrator เป็น **finite StateGraph** 8 nodes (LangGraph) ไม่ใช่ free-form tool loop: ทุก node เรียก tool ได้ไม่เกิน 1 ตัว
  จาก allowlist 5 ตัว (`external_data.query_context@1`, `data_integration.create_snapshot@1`,
  `risk_knowledge.build_evidence_package@1`, `decision_engine.create_decision@1`, `recommendation.create_recommendation@1`)
  แต่ละ tool ผูกกับ stage ที่อนุญาต, input/output contract, timeout, attempts — เรียกผิด stage/ผิด type = `POLICY_VALIDATION_FAILED`
- Budgets บังคับจริง: steps 12, tool calls 10, total 45 s, per-tool ≤ 12 s, LLM calls 2 — เกิน = FAILED พร้อม stable error code
- Consistency gates ระหว่าง node: snapshot↔request/revision, package↔snapshot, decision↔request+snapshot,
  recommendation↔request + locked action + decision id — ไม่มีการ "ซ่อม" ผลลัพธ์ระหว่างทาง
- Intent rules-first (EMERGENCY > hint > follow-up > CHECK_SAFETY > PLAN_TRIP); EMERGENCY shortcut ไม่เรียก provider ใด ๆ
- Missing/unconfirmed origin/destination/departure ⇒ `NEEDS_INPUT` + `missing_fields` (ไม่เดา); resume = run ใหม่ (new request_id)
  ที่ผูกด้วย conversation และ `supersedes_request_id`, ต้อง trip เดิม
- Follow-up: คำถามเชิงข้อมูล (why/source/explain) ใช้ snapshot เดิมถ้าอายุ < 15 นาที; คำถาม "ตอนนี้/still/now/storm" บังคับ refresh
- Progress stream ตาม SSE contract ของคน 2: Redis stream + in-memory mirror, event_id monotonic, ไม่มีพิกัด/prompt/token
- Cancel แบบ cooperative (ตรวจทุก node boundary + ระหว่างรอ tool); state สุดท้าย `CANCELLED`
- Audit: `agent.tool_calls` เก็บเฉพาะ tool, input sha256, status, duration, error code; `agent.runs` เก็บ compact state
- Injection ใน question ถูก flag เป็น limitation `USER_TEXT_FLAGGED_AS_INSTRUCTION_ATTEMPT` และไม่มีทางเปลี่ยน locked action
  (ทดสอบ: decision AVOID ยังคง AVOID แม้ question สั่งให้ตอบ NORMAL)
- 12 tests, ruff, mypy strict ผ่านทั้งหมด

## 3. Acceptance checklist

- [x] graph finite, versioned, no self-loops — `graph/builder.py`, `test_graph_is_finite_and_versioned`
- [x] allowlisted tools with stage/type/budget/timeouts — `tools/registry.py`, `GET /internal/v1/tools`
- [x] budgets enforced with stable error — `test_tool_budget_enforced`
- [x] needs-input never guesses; resume linked, same trip — `test_needs_input_then_resume`
- [x] emergency shortcut with zero provider calls — `test_emergency_shortcut_makes_no_provider_calls`
- [x] provider outage → FAILED `DEPENDENCY_UNAVAILABLE`, retryable, no bypass — `test_provider_outage_fails_with_stable_code`
- [x] malformed downstream payload rejected at the contract boundary — `test_malformed_downstream_is_rejected`
- [x] cancel stops the run — `test_cancel_stops_run`
- [x] follow-up reuse vs refresh rules — `test_followup_reuses_fresh_snapshot_but_current_question_refreshes`
- [x] progress events monotonic, stage order fixed, no sensitive fields — `test_full_run_completes_with_contract_chain`
- [x] locked action cannot be altered by user text — `test_injection_in_question_cannot_change_locked_action`
- [ ] Real E2E against live services in Docker — pending compose run (integration phase)
- [ ] LLM intent fallback (`OPENAI_INTENT_MODEL`) — wired as optional budgeted call, not exercised without a key

## 4. Implemented

| Feature | Entry point | Status |
| --- | --- | --- |
| Settings + budgets | `app/settings.py` | Complete |
| Agent state | `app/graph/state.py` | Complete |
| Tool registry / runner / budget / audit records | `app/tools/registry.py` | Complete |
| Intent + required-field rules + injection heuristics | `app/graph/intent.py` | Complete |
| Graph nodes with consistency gates + degraded propagation | `app/graph/nodes/core.py` | Complete |
| Graph builder + checksum | `app/graph/builder.py` | Complete |
| Run manager (task, timeout, cancel, error mapping, persistence) | `app/graph/runner.py` | Complete |
| Progress publisher (Redis stream + state key) | `app/progress/publisher.py` | Complete |
| Repository (`agent.runs`, `agent.tool_calls`, in-memory fallback) | `app/repositories/repo.py`, `migrations/versions/0001_agent_baseline.py` | Complete |
| Internal API | `app/api/internal.py` | Complete |
| Dockerfile (multi-stage, non-root, healthcheck) | `Dockerfile` | Complete |

## 5. Decisions

- **Resume is a new run**, never a mutation of the old state: keeps the audit trail append-only and lets คน 2 keep one
  `request_id` per assessment attempt. The new run records `supersedes_request_id`.
- **Snapshot reuse only for informational follow-ups** and only while younger than 15 min; anything mentioning current
  conditions refreshes. Reuse skips external-data + integration but still runs risk/decision/recommendation so the
  answer is re-locked by the policy, not paraphrased from memory.
- The evidence package is keyed by its **snapshot's** `request_id` (matches risk-knowledge), while decision and
  recommendation are keyed by the **current** request (matches decision-engine/recommendation) — the gates check exactly
  those pairs.
- `PARTIAL` whenever any downstream reported `degraded_services` (e.g. ORS unavailable, rule baseline instead of model)
  even if the recommendation itself says COMPLETED — honesty over optimism.

## 6. API/contract

Consumes `AgentRunRequest`; produces `RunRef`, `RunState`, `RunProgressEvent[]` (contract 1.0.0). Errors use the shared
envelope with `POLICY_VALIDATION_FAILED`, `DEPENDENCY_UNAVAILABLE`, `DEPENDENCY_TIMEOUT`, `CONFLICT`, `FORBIDDEN`,
`VALIDATION_ERROR`, `NOT_FOUND`.

## 9. Configuration

| Variable | Effect |
| --- | --- |
| `*_SERVICE_URL` (5) | downstream base URLs |
| `MAX_AGENT_STEPS`, `MAX_TOOL_CALLS`, `AGENT_TOTAL_TIMEOUT_SECONDS`, `AGENT_TOOL_TIMEOUT_SECONDS`, `MAX_LLM_CALLS` | budgets |
| `FOLLOWUP_REUSE_MAX_AGE_SECONDS` (900) | snapshot reuse window |
| `RUN_STATE_TTL_SECONDS` (86400) | Redis run-state TTL |
| `POSTGRES_AGENT_PASSWORD`, `REDIS_URL`, `SERVICE_AUTH_TOKEN` | infra/auth |
| `OPENAI_API_KEY`, `OPENAI_INTENT_MODEL` | optional LLM intent fallback (rules always run first) |

## 10. Tests

`uv run pytest -q` → 12 passed; `ruff check`, `mypy --strict` clean (18 source files).

## 13. Problems

| Problem | Resolution |
| --- | --- |
| Follow-up reuse failed the evidence gate (`package.request_id` was the old run's) | gate now compares the package to its snapshot's request id, which is how risk-knowledge keys it |
| Test stub keyed decisions by the package's request id (unlike the real decision-engine) | stub corrected to mirror decision-engine; the gate `decision.request_id == current request` stays strict |
| `run_failed` log had no reason | added the (non-sensitive, our own) message to the structured log |
| Live stack: follow-up rejected by decision-engine (`REQUEST_MISMATCH`) because the package kept the reused snapshot's request id | `EvidencePackageRequest.request_id` — the package is owned by the current request; every downstream gate stays strict |
| Live stack: reassessment after apply-route reused the previous revision's snapshot (`REVISION_MISMATCH`) | reuse only for the same trip **and** revision; a follow-up without a question always fetches fresh data |

## 15. Limitations

| Limitation | Impact | Next |
| --- | --- | --- |
| No live run against real services yet | integration bugs (auth headers, envelope shapes) may surface in compose | integration phase E2E |
| Cancellation is cooperative | a tool call in flight completes (≤ 12 s) before the run stops | acceptable per plan; Redis cancel flag is honoured before the next call |
| Intent classification is keyword-based (TH/EN) | unusual phrasing may map to PLAN_TRIP/CHECK_SAFETY | optional LLM fallback behind `OPENAI_INTENT_MODEL` |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 2 (API) | `POST/GET /internal/v1/runs`, events list, Redis stream name `sta:{env}:run:{id}:events`, state key `sta:{env}:run:{id}:status` | bridge SSE from the stream; map `NEEDS_INPUT.missing_fields` to the UI; use `Idempotency-Key` → `request_id` |
| คน 1 (Web) | progress stage enum order, terminal statuses, degraded/limitations semantics | show PARTIAL honestly; never hide `degraded_services` |
| คน 4–8 | tool contracts + stage mapping in `GET /internal/v1/tools` | keep response envelopes contract-valid; 4xx is final (agent does not retry) |

ผู้จัดทำ: คน 3 (simulated) · วันที่: 2026-09-17
