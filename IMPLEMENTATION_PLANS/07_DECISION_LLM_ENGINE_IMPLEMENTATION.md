# คนที่ 7 — Decision and LLM Engine Implementation Plan

## Mission

สร้าง decision engine ที่ตรวจความสอดคล้องของ evidence แล้วเลือก `NORMAL`, `CHANGE_ROUTE`, `DELAY` หรือ `AVOID` ด้วย deterministic, versioned, testable policy จากนั้น lock actionและให้ LLMเขียนคำอธิบายแบบ structuredจาก facts/citationsที่อนุมัติเท่านั้น พร้อม post-validationและ fixed-template fallback

ความสำคัญ: เป็น safety boundaryสุดท้ายก่อนแสดงคำแนะนำ ถ้าไม่เสร็จระบบไม่มี final action หากปล่อย LLMตัดสินเองอาจ hallucinate, ลด official warning, เปลี่ยนตัวเลขหรือแนะนำทางปิด

## Ownership and dependencies

- `services/decision-engine/**`
- schema PostgreSQL `decision`
- decision policy/prompt versionsและ approval records
- golden/property/red-team cases
- internal decision OpenAPIร่วมกับคน 3/6/8

Consumes snapshot quality + risk/RAG/routesจากคน 5/6; produces `DecisionResult`ให้คน 3/8

## Stack

- Python 3.12, FastAPI, Pydantic v2
- Python decision tables loadedจาก versioned YAML/JSONที่ validate schema
- Jinja2 strict templates; JSON Schema strict output
- OpenAI Python SDK Responses API, modelจาก env `OPENAI_EXPLAINER_MODEL`
- httpx/Tenacity, Redis short cache, PostgreSQL audit/policy registry
- structlog/OpenTelemetry/Prometheus
- pytest, Hypothesis property tests, golden snapshots, adversarial evaluation

## Target structure

```text
services/decision-engine/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ domain/
│  │  ├─ inputs.py
│  │  ├─ decision.py
│  │  └─ validation.py
│  ├─ policy/
│  │  ├─ loader.py
│  │  ├─ evaluator.py
│  │  ├─ confidence.py
│  │  └─ escalation.py
│  ├─ llm/
│  │  ├─ client.py
│  │  ├─ prompts.py
│  │  ├─ schemas.py
│  │  ├─ validators.py
│  │  └─ fallback.py
│  ├─ repositories/
│  └─ settings.py
├─ policies/v1/decision-table.yaml
├─ prompts/v1/explain.j2
├─ schemas/decision-policy.schema.json
├─ migrations/
├─ tests/golden/
└─ Dockerfile
```

## Separation of duties — invariant

Decision engine:

- ตรวจ same request/snapshot/route/time/version
- ให้ priority official warning/closure
- คำนวณ action/confidence/escalationด้วย rules
- สร้าง allowed evidence packageและ lock action

LLM:

- เรียบเรียง `short_summary`, `reasons`, `immediate_actions`, `limitations` ตามภาษา
- อ้าง citation IDsที่ให้เท่านั้น
- ห้ามเพิ่มข้อเท็จจริง ตัวเลข contact URL route หรือ actionใหม่

Post-validator:

- actionตรง lock
- citationทุกตัวอยู่ allowlist
- number/entityสำคัญ match evidence
- emergency instructionsไม่ขัด official text/policy
- output schema/language/length/safety phraseครบ

ถ้า LLM fail/timeout/validation failหลัง retryจำกัด ใช้ deterministic localized template ไม่ส่ง raw output

## Input consistency gate

Reject/route to degraded/escalationเมื่อ:

- `request_id`, `snapshot_id`, route IDsไม่ตรง
- travel window/route revisionไม่ตรง
- contract/feature/model version unsupported
- official alertหมดอายุแต่ยังถูกใช้ หรือ active alertหายจาก packageโดยไม่อธิบาย
- quality gate `BLOCK`
- route selectedเป็น closed/invalid
- citation document expired/wrong geography

ห้ามพยายามให้ LLMแก้ inconsistency

## Policy priority and rules

Policy tableต้องกำหนด priorityสูงไปต่ำและ first-match/merge semanticsชัดเจน Baseline:

1. active official closure/no-go/mandatory evacuation intersect route/time -> `AVOID`
2. risk HIGHและไม่มี usable safe route -> `AVOID`
3. risk HIGH/MEDIUMและมี materially safer usable routeตาม threshold -> `CHANGE_ROUTE`
4. risk time-dependentและ future windowลดต่ำกว่า thresholdโดยไม่มี closure -> `DELAY`
5. risk LOW, no active restriction, evidence qualityผ่าน -> `NORMAL`
6. insufficient/conflicting critical evidence -> conservative actionตาม matrix + `escalation_required` ไม่ default NORMAL

“materially safer” และ “time-dependent” ต้องเป็น numeric/versioned thresholds เช่น delta risk/minimum confidence/max delay ไม่เขียน vague conditionใน code

Monotonic safety property:

- เพิ่ม risk/severity/official restrictionโดย inputsอื่นเท่าเดิมต้องไม่สร้าง actionที่อ่อนกว่า เว้นแต่มี explicit reviewed rule
- official warningไม่ถูก model/LLM lower
- data qualityลดต่ำไม่เพิ่ม confidenceหรือเปลี่ยน UNKNOWNเป็น NORMAL

## Confidence and escalation

Confidenceไม่เท่ากับ model probabilityเพียงค่าเดียว คำนวณจาก:

- calibrated risk uncertainty
- evidence quality/coverage/freshness
- source conflict
- route alternative quality
- rule specificity
- LLM validation **ไม่เพิ่ม** safety confidence; เป็น presentation quality

Escalateเมื่อ configurable conditions เช่น official sourcesขัดกัน, high uncertainty near threshold, critical missing/low coverage, unsupported region, unsafe user feedback replay, policy/model mismatch

## LLM evidence package

ส่งเฉพาะ:

- locked action/risk/confidenceและ reason codes
- validated route trade-offs
- concise weather/transport/disaster factsพร้อม units/time
- retrieved evidence snippetsพร้อม citation IDs/authority/page/section
- limitations/degraded services
- locale/style constraintsและ output JSON schema

ไม่ส่ง secret, profile medical info, exact live locationที่ไม่จำเป็น, provider raw HTML, user instructionใน system block หรือ chain-of-thought

ใช้ OpenAI Responses APIผ่าน server only โดยส่ง `text.format` เป็น JSON Schema, `strict: true`, `additionalProperties: false` และระบุทุก propertyใน `required` (field optionalให้ typeรวม `null`) ตั้ง temperature 0/ต่ำ, token limit, timeout และ `store: false` เป็นค่าเริ่มต้นสำหรับข้อมูล travel-safety เว้นแต่ data-retention reviewอนุมัติเป็นอย่างอื่น ต้อง handle `refusal` และ incomplete outputจาก max token/เหตุอื่นก่อน parse ห้ามถือว่า strict schemaทำให้ทุก responseสำเร็จเสมอ

เอกสารอ้างอิง implementation: <https://developers.openai.com/api/docs/guides/structured-outputs> และ <https://developers.openai.com/api/reference/resources/responses/methods/create>

## Output validation details

1. Pydantic/JSON Schema strict; extra fields forbidden
2. `action_code` byte-equal locked action
3. citations subsetของ provided IDsและทุก factual reasonมี citation/reason code
4. numbers extraction/normalizationเทียบ allowed fact tableด้วย toleranceที่กำหนด
5. contact/URLห้ามมาจาก LLM; Module 8แนบจาก verified data
6. banned/overclaim phrases เช่น guarantee of safety ต้องถูก reject
7. emergency stepsต้องเป็น approved structured steps; LLM paraphraseได้เฉพาะ policyอนุญาต
8. locale and max length; sanitize downstreamอีกชั้น

Retryหนึ่งครั้งด้วย validation errorsที่ไม่เผย sensitive content; ถ้ายัง failใช้ fallback

## Audit trace

เก็บ:

- input object IDs/hashes/versions ไม่ duplicate sensitive payload
- policy/prompt/model/schema versions/checksums
- rules evaluated/fired/priorities
- locked action/confidence/escalation
- evidence/citation IDs
- LLM request metadata token/latency/statusและ output hash
- validator results/retry/fallback reason

ห้ามเก็บ hidden chain-of-thought; reasoning auditเป็น rule/evidence traceเท่านั้น

## Implementation steps

### Phase 0 — Policy specification and approval

1. ร่วมคน 6/8นิยาม input/output/reason codes
2. เขียน decision table v1 + JSON Schema + human-readable policy doc
3. ระบุ thresholds/priority/tie/conflict/degraded/escalationทั้งหมด
4. สร้าง approval record/checksumและ rollback previous version
5. สร้าง truth table/golden scenariosก่อน implementation

Exit: Team Lead + คน 6/8 review policy v1

### Phase 1 — Service/audit foundation

1. FastAPI/internal auth/health/metrics/settings
2. PostgreSQL migrations policy/prompt/audit tables
3. policy loader verify schema/checksum/status=APPROVED
4. Docker non-root; missing active policyทำ readiness fail
5. input consistency validator

### Phase 2 — Deterministic evaluator

1. implement pure rule evaluatorไม่พึ่ง LLM/network
2. exact priority/multi-rule trace
3. confidence/escalation functions
4. threshold boundary testsทุกค่าก่อน/เท่ากับ/หลัง
5. Hypothesis monotonic safety properties
6. fixed localized response skeletonจาก reason codes

Exit: ทุก golden scenarioได้ actionตรงโดยปิด LLM

### Phase 3 — LLM explanation

1. versioned Jinja strict promptแยก trusted instruction/evidence/untrusted passages
2. OpenAI Responses API structured output client (`text.format`, strict schema, `store: false`); model env/config
3. token/time/cost/retry limitsและ error mapping
4. handle refusal/incomplete/length stop แล้ว validate action/citation/number/entity/safety/language
5. deterministic fallback templates Thai/English
6. injection/invalid JSON/hallucinated citation/action change tests

Exit: LLM unavailableแล้วยังคืน safe valid decision explanationได้

### Phase 4 — API, caching and observability

1. POST decision/idempotency by input hash+versions
2. validation endpoint/policy metadata endpoint
3. cache explanationเฉพาะ identical evidence/prompt/model/localeและ TTLไม่เกิน evidence expiry
4. traces/metrics: rule counts/actions/confidence/escalation/LLM latency/validation failure/fallback
5. redact prompts/evidenceตาม config; debug logging production off

### Phase 5 — Red-team and integration

1. conflicting official/general source
2. low model score + official closure
3. malicious provider/RAG/user text instructing action/key exfiltration
4. wrong/stale citation, altered number/contact, unsupported locale
5. higher risk monotonic property across generated combinations
6. real evidence package E2Eกับคน 6/8
7. policy/model/prompt rollback drill

### Phase 6 — Final handoff

1. run full golden/property/adversarial suiteและรายงาน counts
2. verify audit replayจาก IDs/versions
3. validate fallbackเปิดเมื่อ remove LLM key/timeout
4. docs decision table/thresholds/approval/rollback/completion report

## Test matrix

| Area | Must cover |
| --- | --- |
| Consistency | mismatched IDs/time/route/version, expired evidence, closed selected route |
| Rules | every rule, overlap/priority/tie, all threshold boundaries |
| Properties | monotonic safety, official priority, lower quality not higher confidence |
| Confidence | uncertainty/coverage/conflict/degraded combinations |
| LLM | valid, timeout, rate limit, invalid JSON, action change, invented citation/number/contact |
| Injection | user/provider/RAG prompt injection, HTML/script, multilingual attacks |
| Fallback | Thai/English template, missing optional facts, LLM disabled |
| Audit | hashes/versions/rules/validator result, no PII/secret/CoT |

## Acceptance checklist

- [ ] actionมาจาก approved deterministic policyเท่านั้น
- [ ] official warning/closure priorityและ monotonic safety propertyผ่าน
- [ ] input route/time/version consistency gateทำงาน
- [ ] confidence/escalationโปร่งใสและ versioned
- [ ] LLMเห็นเฉพาะ validated evidenceและเปลี่ยน actionไม่ได้
- [ ] citations/numbers/entities validateหลัง generation
- [ ] LLMล่มยังได้ valid fixed-template response
- [ ] audit replayได้โดยไม่เก็บ PII/chain-of-thought
- [ ] policy/prompt/model rollbackได้
- [ ] real E2Eกับคน 6/8ผ่านใน Docker

## Branch/commit/PR breakdown

1. `contract/07-decision-result-schema`
2. `feat/07-policy-registry`
3. `feat/07-deterministic-evaluator`
4. `feat/07-confidence-escalation`
5. `feat/07-llm-explainer`
6. `feat/07-output-validation-fallback`
7. `feat/07-audit-observability`
8. `test/07-golden-property-redteam`

## Completion report requirements

สร้าง `docs/handoffs/M07-decision-llm.md` พร้อม policy table/version/checksum/approvals, threshold boundary table, confidence/escalation formula, prompt/model/version, validator/fallback behavior, golden/property/red-team results, audit sampleที่ sanitizeแล้ว, latency/token/costและ rollback drill
