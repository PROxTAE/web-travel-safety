# คนที่ 3 — Travel AI Agent Implementation Plan

## Mission

สร้าง LangGraph orchestration แบบ finite state machine ที่รับ `TravelRequest` ที่ normalized แล้ว วาง execution plan จาก intent/ข้อมูลที่มี เรียก tools ที่อนุมัติไปยัง Module 04–08 ด้วย budget/timeout/cancellation ที่ชัดเจน เก็บ checkpoint สำหรับ follow-up และคืนผลที่ traceกลับไปยัง evidenceได้ โดย agent ห้ามตัดสิน safetyจาก free text และห้ามมี unlimited loop

ความสำคัญ: Agent คือผู้ควบคุมลำดับการทำงานทั้งระบบ หากไม่เสร็จ backendไม่สามารถเปลี่ยน requestเป็น recommendation หากออกแบบผิดอาจเรียก providerไม่สิ้นสุด, ใช้ข้อมูลคนละ route/time, เชื่อ prompt injection หรือส่งผลไม่ครบไป decision engine

## Ownership and dependencies

- `services/agent/**`
- internal agent OpenAPIร่วมกับคน 2
- schema `agent` และ LangGraph checkpointer tables
- agent evaluation/golden scenarios

Consumes:

- คน 2: normalized request, cancellation, conversation context
- คน 4: real external context
- คน 5: integrated immutable snapshot
- คน 6: risk/RAG/route evidence package
- คน 7: locked decision/explanation
- คน 8: final recommendation

Produces:

- run lifecycle/status/progress สำหรับคน 2/1
- tool call trace, checkpoint, result references

## Stack

- Python 3.12, FastAPI, Pydantic v2
- LangGraph StateGraph + async nodes + PostgreSQL checkpointer
- httpx async clients, Tenacityเฉพาะ transient retry
- Redis Streams/PubSubสำหรับ progress event delivery
- OpenTelemetry; Langfuse optionalผ่าน feature flagและต้อง redact
- pytest/pytest-asyncio/respx + property/golden tests
- LLMใช้เฉพาะ intent/extractionเมื่อจำเป็นและต้อง structured output; simple known intentใช้ deterministic classifierก่อน

## Target structure

```text
services/agent/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ graph/
│  │  ├─ builder.py
│  │  ├─ state.py
│  │  ├─ routing.py
│  │  └─ nodes/
│  │     ├─ validate_input.py
│  │     ├─ classify_intent.py
│  │     ├─ check_required_fields.py
│  │     ├─ fetch_external_data.py
│  │     ├─ integrate_data.py
│  │     ├─ build_evidence.py
│  │     ├─ make_decision.py
│  │     ├─ format_recommendation.py
│  │     └─ finalize.py
│  ├─ tools/
│  │  ├─ registry.py
│  │  ├─ schemas.py
│  │  └─ clients/
│  ├─ checkpoints/
│  ├─ budgets/
│  ├─ progress/
│  ├─ observability/
│  └─ settings.py
├─ prompts/                 # versioned, only intent/extraction support
├─ tests/
├─ pyproject.toml
├─ uv.lock
└─ Dockerfile
```

## AgentState contract

ใช้ `TypedDict`/dataclassที่ serializeได้; DataFrame, client object, secret ห้ามอยู่ใน checkpoint

```text
identity:
  request_id, correlation_id, trip_id, conversation_id, user_scope_hash
input:
  travel_request, approved_context_refs, intent, missing_fields
plan:
  graph_version, required_tools, current_stage
observations:
  external_context_ref, snapshot_id, evidence_package_ref
result:
  risk_assessments, evidence_ids, route_ids, decision_id, recommendation_id
quality:
  quality_flags, degraded_services, conflicts, freshness
control:
  status, errors, step_count, tool_call_count, token_usage, estimated_cost,
  started_at, deadline_at, cancelled, remaining_budget
versions:
  contract, graph, prompt, policy, provider_configs
```

Checkpointเก็บ references/metadataเป็นหลัก ไม่ duplicate provider raw payloadหรือ sensitive profile

## Graph design

```text
START
  -> validate_input
  -> classify_intent
  -> check_required_fields
       ├─ missing critical -> emit NEEDS_INPUT -> END/interrupt
       └─ complete
            -> fetch_external_data
            -> integrate_data
            -> build_evidence (risk + RAG + routes through Module 06)
            -> validate_evidence
                 ├─ insufficient/retry budget -> degraded_or_escalate
                 └─ sufficient -> make_decision (Module 07)
            -> format_recommendation (Module 08)
            -> validate_final_contract
            -> finalize -> END
```

Follow-up:

- informational questionที่ใช้ evidenceเดิมได้เมื่อยัง fresh -> resumeจาก checkpointและ retrieve/decision/formatตามจำเป็น
- คำถาม current safety, route/time change หรือ TTLหมด -> fetchใหม่และสร้าง snapshotใหม่
- trip updateไม่แก้ stateเก่า; สร้าง runใหม่ link `supersedes_request_id`

## Tool registry rules

Tool ทุกตัวต้องประกาศ:

- stable name/version เช่น `external_data.query_context@1`
- Pydantic input/output schemaจาก generated contract
- allowed intent/stage
- base URLจาก env allowlist; agentห้ามรับ URLจาก model/user
- connect/read/total timeout
- retryable error codesและ max attempts
- permission/consent requirements
- cost/quota weightและ redaction fields
- idempotency behavior

Allowlisted tools MVP:

1. `external_data.query_context`
2. `data_integration.create_snapshot`
3. `risk_knowledge.build_evidence_package`
4. `decision_engine.create_decision`
5. `recommendation.create_recommendation`

ห้ามให้ LLMเห็น generic HTTP, shell, filesystem, database หรือ arbitrary Python tool

## Budgets and stop conditions

ทุกค่า configได้และบันทึก version:

```text
MAX_AGENT_STEPS=12
MAX_TOOL_CALLS=10
AGENT_TOTAL_TIMEOUT_SECONDS=45
AGENT_TOOL_TIMEOUT_SECONDS=12
MAX_LLM_CALLS=2
MAX_INPUT_TOKENS=...
MAX_OUTPUT_TOKENS=...
MAX_ESTIMATED_COST_USD=...
```

Stopทันทีเมื่อ:

- cancellation flag
- deadline/step/tool/token/cost exceeded
- critical inputหาย -> `NEEDS_INPUT`
- final recommendation validateผ่าน -> `COMPLETED/PARTIAL`
- official/safety conflictที่ policyต้อง human review -> escalation result
- non-retryable contract/auth error -> failedพร้อม stable code

Retriesเกิดใน tool client ไม่ย้อน graphแบบไม่จำกัด; ใช้ exponential backoff+jitterและ deadlineรวม

## Progress events

ก่อน/หลังแต่ละ node publish eventตาม SSE stage contract โดย eventไม่มี chain-of-thought:

- stage, status, user-safe message key, started/completed time
- optional provider/service nameสำหรับ degraded
- error codeที่ safe
- event ID monotonicต่อ run

ห้าม publish hidden reasoning, prompt, exact coordinates, raw tool payload หรือ token

## Implementation steps

### Phase 0 — Contract and graph specification

1. อ่าน internal contractsทั้ง 04–08และทำ tool I/O matrix
2. วาด state/edge/error/cancel/resume diagramใน `docs/diagrams/agent-state.md`
3. นิยาม intent: `PLAN_TRIP`, `CHECK_SAFETY`, `ASK_INFORMATION`, `FOLLOW_UP`, `EMERGENCY`; emergencyต้องส่งทางลัดไปข้อมูลฉุกเฉินแต่ไม่ auto-contact
4. นิยาม critical missing fieldsต่อ intentและ freshness rule
5. ตกลง progress/SSEกับคน 2 และ response refsกับคน 8

Exit: graph spec + AgentState schema reviewครบ

### Phase 1 — Service/graph scaffold

1. FastAPI/internal auth/health/readiness/metrics
2. LangGraph builderพร้อม no-op deterministic nodesที่ยังไม่เรียก external; ห้าม exposeเป็น demo result
3. compile graph validation; graph version/checksum
4. PostgreSQL checkpointer migrations; thread ID = conversation ID, run IDแยก
5. Redis progress publisherที่ idempotentและ event ordering
6. Docker non-rootและ config validation

Exit: run validation/needs-input/cancel lifecycleผ่านโดยไม่มี fake recommendation

### Phase 2 — Intent and required information

1. deterministic intent rulesสำหรับ explicit UI actionsก่อน
2. LLM structured classifierเฉพาะ ambiguous text; promptแยก system/user/provider data
3. entity extractionใช้ request structured fieldsเป็น source of truth; modelห้าม override confirmed coordinates/time
4. missing critical field -> `NEEDS_INPUT`พร้อม field names/message key
5. adversarial/prompt injection tests; provider/RAG textห้ามเข้า classifier instruction

Exit: golden intent/field casesผ่าน Thai/English

### Phase 3 — Typed tool clients

1. generate clientsจาก OpenAPI; ห้าม handcraft duplicate schema
2. registry enforce allowlist/stage/permission/budget
3. propagate request/correlation/trace/idempotency/deadline
4. timeout, cancellation, retry and circuit/bulkhead isolation
5. response schema validationและ safe error mapping
6. tool call auditเก็บ hash/status/timing ไม่เก็บ sensitive raw body

Exit: contract testsกับแต่ละ service stub containerผ่าน และ malformed responseถูก reject

### Phase 4 — Main orchestration path

1. `fetch_external_data`: one combined callหรือ parallel approved calls, เก็บ ref/quality
2. `integrate_data`: สร้าง immutable snapshotและตรวจ request/route/time match
3. `build_evidence`: risk/RAG/routes; validate versions/freshness/conflicts
4. `make_decision`: ส่งเฉพาะ validated packageไปคน 7
5. `format_recommendation`: ส่ง decision lockedไปคน 8
6. final schema checkและ terminal status
7. publish progress/degradedทุกขั้น

Exit: real full-stack runจาก normalized requestถึง recommendation ID

### Phase 5 — Degraded, escalation and follow-up

1. สร้าง error policy table per dependency/error/available evidence
2. providerบางตัวล่ม -> continueถ้า policyอนุญาต, mark degraded, lower confidence
3. data integration/risk/decision contract failure -> stopไม่ bypass
4. official conflict/insufficient evidence -> conservative/escalationตามคน 7
5. resume checkpointด้วย follow-up; validate user/conversation ownership tokenจาก API context
6. refresh current dataเมื่อ TTLหมดหรือ trip/time/routeเปลี่ยน

Exit: failure matrix/golden replayผ่านและไม่ reuse stale evidenceผิดกรณี

### Phase 6 — Observability and evaluation

1. trace spanต่อ node/toolพร้อม versions/duration; redact input
2. metrics: runs/status, node latency, tool errors/retries, budget stops, degraded rate
3. deterministic replayใช้ source snapshot refsและ versions; LLM output hash
4. evaluation suiteอย่างน้อย 40 scenarios: normal, hazards, conflict, missing, timeout, injection, Thai/English
5. assert no loops: property testว่า graphจบภายใน max stepsทุก generated state

### Phase 7 — Final integration/handoff

1. real providers E2Eหลาย route/time; capture request/trace IDs
2. restartกลาง runและยืนยัน checkpoint/resumeหรือ documented fail-safe
3. concurrent runs/bulkhead/load test
4. verify checkpoint/logไม่มี PII/secret/raw responseที่เกิน policy
5. docs, graph diagram, tool matrix, completion report, PR

## Required tests

| Type | Cases |
| --- | --- |
| Graph unit | each node/edge, missing fields, stop budgets, cancel, terminal transitions |
| Tool contract | valid/malformed response, timeout, 429/Retry-After, 5xx, auth, cancellation |
| Golden | safe, route change, delay, avoid, official closure, conflicting/stale, no coverage |
| Follow-up | fresh reuse, stale refresh, route change new snapshot, different user rejected |
| Security | prompt injection in question/provider/RAG, arbitrary URL/tool request, PII redaction |
| Resilience | one/all providers fail, DB/Redis restart, decision/LLM failure, duplicate run |
| Property | terminates under budget, action not authored/altered by agent, IDs/time alignment |

## Acceptance checklist

- [ ] graph finiteและไม่มี autonomous generic tool
- [ ] every tool schema/timeout/retry/permission/budgetกำหนดชัด
- [ ] independent work parallelizeได้แต่ resultsรวมก่อน decision
- [ ] missing critical dataถามผู้ใช้แทนการเดา
- [ ] stale/current follow-up behaviorถูกต้อง
- [ ] checkpoint/resumeและ cancellationทำงาน
- [ ] agentไม่ตัดสิน risk/actionเองและไม่เปลี่ยน locked decision
- [ ] progress safeสำหรับ UIและ traceได้ถึง evidence/version
- [ ] max step/tool/token/cost/time enforced
- [ ] real E2E + degraded scenariosผ่านใน Docker

## Branch/commit/PR breakdown

1. `contract/03-agent-run-schema` — AgentState/run/progress contracts
2. `feat/03-agent-graph` — graph lifecycle/checkpointer/status
3. `feat/03-intent-extraction` — deterministic + structured LLM classifier
4. `feat/03-tool-registry` — generated clients/allowlist/budgets
5. `feat/03-orchestration-path` — 04->05->06->07->08 flow
6. `feat/03-followup-degraded` — resume/freshness/error/escalation
7. `test/03-agent-evaluation` — golden/property/security/resilience

## Completion report requirements

สร้าง `docs/handoffs/M03-travel-agent.md` และระบุ graph diagram, node/edge table, tool registry, timeout/retry/budget values, checkpoint retention, golden scenario results, degraded matrix, trace sample และ known limitations โดยห้ามแนบ chain-of-thought/prompt secret

