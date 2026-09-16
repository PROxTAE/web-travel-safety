# คนที่ 6 — Risk and Knowledge Services Implementation Plan

## Mission

สร้าง 3 capabilityใน service เดียว: (1) local risk model ที่เทรน/serveจากข้อมูลจริงและอธิบายด้วย reason codes (2) Disaster RAG จากเอกสารหน่วยงานที่อนุมัติพร้อม citationตรวจสอบได้ (3) route evaluation/ranking ที่วัด exposureตลอดเส้นทางและเคารพ closure/evacuation hard constraints

ความสำคัญ: เป็นหลักฐานเชิงความเสี่ยง/ความรู้/ทางเลือกก่อน decision หากไม่เสร็จระบบไม่มี local AI model, ไม่มี emergency guidanceที่ grounded และไม่รู้ว่า routeใดปลอดภัยกว่า หากผิดอาจเกิด false negative HIGH risk หรือแนะนำ routeผ่านเขตปิด

## Ownership and dependencies

- `services/risk-knowledge/**`
- schema PostgreSQL `knowledge`
- Qdrant collectionsและ index lifecycle
- model training/evaluation/registry metadata + model card
- approved knowledge manifest/chunking/index/evaluation
- route exposure/ranking API

Consumes snapshot/feature schemaจากคน 5และ route/provider dataจากคน 4ผ่าน snapshot; produces evidence packageให้คน 3/7

## Stack

- Python 3.12, FastAPI, Pydantic v2
- scikit-learn baseline (`LogisticRegression` หรือ `HistGradientBoostingClassifier`) + calibration; joblib/skops/ONNXตาม security review
- MLflowสำหรับ experiment/registry metadataใน local `training` profile
- NumPy/Polars; shared feature packageจากคน 5
- SHAP optional; reason-code mappingต้องมีเสมอ
- Sentence Transformers multilingual model, Qdrant, `rank-bm25`, cross-encoder rerankerที่ผ่าน latency budget
- pypdf/doc parser; checksum/metadata validation
- Shapely/PostGIS/GeoAlchemy2สำหรับ route exposure
- Evidently/Prometheusสำหรับ drift/monitoring
- pytest/Hypothesis + offline evaluation scripts

## Target structure

```text
services/risk-knowledge/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ risk/
│  │  ├─ features.py
│  │  ├─ model_loader.py
│  │  ├─ inference.py
│  │  ├─ thresholds.py
│  │  ├─ overrides.py
│  │  └─ explanations.py
│  ├─ knowledge/
│  │  ├─ ingest.py
│  │  ├─ chunking.py
│  │  ├─ embeddings.py
│  │  ├─ retrieve.py
│  │  ├─ rerank.py
│  │  └─ citations.py
│  ├─ routes/
│  │  ├─ exposure.py
│  │  ├─ constraints.py
│  │  └─ ranking.py
│  ├─ repositories/
│  └─ settings.py
├─ training/
│  ├─ build_dataset.py
│  ├─ train.py
│  ├─ evaluate.py
│  ├─ promote.py
│  └─ model_card_template.md
├─ knowledge/
│  ├─ sources.yaml
│  └─ README.md
├─ migrations/
├─ tests/
└─ Dockerfile
```

## Part A — Local risk model

### Problem definition

Prediction unit = one route candidate + departure window + immutable snapshot

Output:

- continuous risk score/probability
- calibrated uncertainty/confidence
- `LOW/MEDIUM/HIGH` from versioned thresholds
- controlled reason codes
- safety overrides applied/required
- model/feature/schema versions

Model scoreไม่ใช่ final action คน 7จะเลือก actionจาก score + official constraints + route options + quality

### Real training data rule

- ห้ามสร้าง random/synthetic rowsเป็น training corpusหลัก
- ดึง historical weather/provider data, USGS/GDACS/EONET event history, real GTFS/service alertsและ real route geometriesตาม license
- labelต้องมี provenanceและ `label_method`: official alert severity/closure/outcome, reviewed label หรือ documented weak supervision
- split by time **และ** geography; ห้ามสุ่ม row splitที่ route/eventเดียวกันหลุดทั้ง train/test
- cutoffป้องกัน future leakage; featureใช้เฉพาะข้อมูลที่มี ณ prediction time
- เก็บ dataset manifest/checksum/query window/source/license ไม่จำเป็นต้อง commit raw dataที่ termsห้าม
- ถ้าข้อมูล HIGHไม่พอ ต้องรายงาน limitationและใช้ conservative rule override ไม่แต่ง classให้สมดุลโดยไม่เปิดเผย; class weighting/resamplingทำได้เฉพาะ training fold

### Baseline and promotion criteria

1. deterministic rule-only baseline
2. explainable calibrated classifier baseline
3. candidate modelอื่นเพิ่มได้เมื่อมี evidence

Metrics:

- HIGH-risk recallเป็น primary; false-negative rateต้องรายงานพร้อม confidence interval
- precision/recall/F1 per class, PR-AUC, ROC-AUC, Brier score/calibration error
- confusion matrix by geography/season/hazard/mode
- coverage/degraded performance
- latency/memory

Promotionต้องผ่าน thresholdที่ทีม/อาจารย์อนุมัติใน `model_acceptance.yaml`; ห้าม invent targetภายหลังเพื่อให้ modelผ่าน Model registry stage: `CANDIDATE -> APPROVED -> ACTIVE -> RETIRED`, promotionต้องมี approver/checksum

### Safety overrides

ก่อน/หลัง modelตาม policyที่ versioned:

- active official closure/evacuation/no-go -> minimum HIGH/decision input hard constraint
- extreme official warning intersect corridor/time -> HIGH
- missing critical evidence/coverageต่ำ -> UNKNOWN/degraded ไม่ LOW
- modelกับ official source conflict -> official source winsและ flag conflict

Moduleนี้คืน override evidence; final actionยังอยู่คน 7

## Part B — Disaster RAG

### Approved source manifest

`knowledge/sources.yaml` ทุก sourceต้องมี:

```text
document_id, title, authority, source_url, country/region, hazards,
languages, effective_at, expires_at/review_due_at, license,
checksum, ingestion_method, reviewer, review_status
```

รับเฉพาะ government, UN/WHO/recognized authorityหรือ sourceที่ทีมอนุมัติ ห้ามให้ agent ingest URLจาก userแบบอัตโนมัติ

### Ingestion

1. downloadผ่าน allowlist/TLS; record final URL/status/time/checksum
2. malware/type/size check; parse text/page/section
3. preserve heading/procedure boundaries; ห้ามตัด emergency numbered stepsกลางชุด
4. normalize whitespace/languageโดยไม่เปลี่ยนความหมาย
5. chunkพร้อม page/section/authority/effective/expiry/geography/hazard metadata
6. embed, upsert Qdrant collection versionใหม่; record PostgreSQL metadata
7. evaluateก่อน alias `active` switch; old collection rollbackได้

### Retrieval

- queryจาก structured hazard/location/risk/language/action ไม่ใช้ user promptดิบอย่างเดียว
- metadata filter authority/geography/language/effective/expiry
- hybrid BM25 + dense; union candidate -> rerank -> diversity/dedup
- thresholdต่ำไม่คืน passage; state `NO_RELIABLE_KNOWLEDGE_EVIDENCE`
- citationต้อง resolveกลับ document URL/page/section/checksum
- retrieved textเป็น untrusted dataและส่งแยกจาก system instructions

Evaluation: curated query relevance, Recall@K, MRR/nDCG, citation accuracy, expired/wrong-region exclusion, Thai/English, prompt injection documents, latency

## Part C — Route evaluation

- input base `RouteCandidate[]`, snapshot events/weather/closuresและ preferences
- compute exposureต่อ segment/time: hazard intersection length/time, distance decay, severity, forecast probability, transport disruption
- hard constraints: official closure/no-go/evacuation direction; route invalidไม่ rankเป็น usable
- weighted costs: riskก่อนตาม safety policy, แล้ว duration/distance/cost/emissions/preferences
- outputs original/recommended/fastest/lowest-riskโดย labelต้องซื่อสัตย์; routeเดียวอาจได้หลายคุณสมบัติแต่ UI dedup
- expose total time/distance/transfers/risk/exposure/source/freshness/trade-off
- ORS alternative limitationอาจทำให้ไม่มี safer route; ห้าม fabricate geometry

## Implementation steps

### Phase 0 — Contracts and governance

1. lock risk/evidence/route APIกับคน 3/5/7
2. lock feature schema/meaning/null policyกับคน 5
3. สร้าง model acceptance config, model card, data manifestและ approval workflow
4. สร้าง approved knowledge source review processกับ Team Lead
5. นิยาม route exposure formula/hard constraints/version

### Phase 1 — Service/storage foundation

1. FastAPI/internal auth/health/readiness/metrics
2. PostgreSQL migrations + Qdrant collections/aliases + MLflow profile
3. artifact loader verify checksum/signature/approved ACTIVE stageก่อน serve
4. Docker CPU resource limits/non-root; embedding/model warmup readiness
5. deterministic fallback rulesเมื่อ model/RAG unavailable

### Phase 2 — Historical real dataset

1. ingestion scriptsเรียก real historical sourcesตาม provider license
2. reuse integration transforms/features; cutoffและ lineage
3. labeling pipelineพร้อม review sampleและ disagreement report
4. geographic/time split manifest; class distribution/bias audit
5. store dataset checksum/metadataใน MLflow; raw restricted dataอยู่นอก Git

Exit: dataset reproducibleจาก manifestและไม่มี mock/synthetic primary rows

### Phase 3 — Baseline model

1. rule baseline metrics
2. train pipeline preprocessing+classifier+calibrationภายใน CVเพื่อไม่ leak
3. evaluate all metrics/subgroups/threshold boundaries
4. derive LOW/MEDIUM/HIGH thresholdsจาก approved objective
5. reason code mapping/SHAP validation; no causal claim
6. create model card, artifact checksum, promote candidate->approved->active
7. inference endpoint strict feature version/quality check

Exit: active local modelโหลดหลัง restartและ prediction reproducible

### Phase 4 — Safety override and monitoring

1. versioned override table
2. unit/property tests monotonicityและ official priority
3. drift inputs/predictions by location/season/hazard
4. metrics HIGH recall monitoring proxy/feedbackแยกจาก automatic retrain
5. rollback previous model/threshold config

### Phase 5 — Knowledge ingestion/RAG

1. source manifest validatorและ downloader allowlist
2. parsers/chunker preserving procedure/page/section
3. multilingual embedding + Qdrant versioned collection
4. BM25 index + dense retrieval + reranking/filters
5. citation resolverและ expiry guard
6. evaluationชุด Thai/English/wrong region/expired/injection/no evidence
7. release collection aliasและ rollback

### Phase 6 — Route evaluation

1. segment/time exposure joinจาก snapshot
2. hard constraint filtering
3. versioned cost/rankingและ tie-breaking deterministic
4. generate trade-off/reason codesไม่ใช้ LLM
5. route tests hand-calculated geometry/weather/closure

### Phase 7 — Evidence package API

1. `/risk/assess`, `/knowledge/retrieve`, `/routes/evaluate`
2. combined `/evidence/package` run independent subpartsใน parallelเมื่อ safe
3. validate same request/snapshot/route/time/version
4. partial/degraded semanticsหาก RAG/model/routeส่วนหนึ่งล่ม
5. contract/performance/concurrency tests

### Phase 8 — Final verification/handoff

1. full real snapshot -> model/RAG/routes -> evidence package
2. golden disasters/closures/conflicts/missing/Thai/English
3. verify no expired/wrong authority citationและ no closed recommended route
4. resource/latency/load/rollback tests
5. model card/data sheet/source manifest/evaluation/completion report

## Required tests

| Area | Must cover |
| --- | --- |
| Model data | reproducibility, manifest/checksum, leakage, split isolation, class/subgroup distribution |
| Model | calibration, HIGH recall/FN, threshold boundaries, unknown/missing, version mismatch |
| Overrides | official closure/warning, monotonic risk, conflict, model unavailable |
| RAG ingestion | file/type/checksum, procedure chunking, metadata, expired/rejected source |
| Retrieval | relevance/citation, geography/language/effective filter, low confidence, injection text |
| Routes | intersection/time, hard closure, safer/fastest ranking, no alternative, deterministic ties |
| Serving | artifact checksum, active stage, warmup/readiness, concurrent inference, rollback |
| Contract | schema/provenance/version/quality, partial/degraded errors |

## Acceptance checklist

- [ ] local risk modelเทรนจากข้อมูลจริงที่มี manifest/provenance
- [ ] time/location splitและ leakage checksผ่าน
- [ ] HIGH-risk false negativesและ calibrationรายงานชัด
- [ ] official closure/warning override modelได้
- [ ] RAGใช้ approved/current documentsและ citation resolveได้ทุก passage
- [ ] expired/wrong-region/low-confidence evidenceไม่ถูกใช้
- [ ] route rankตรวจ full corridor/timeและ hard constraints
- [ ] outputsมี model/threshold/collection/feature versions
- [ ] rollback model/knowledge collectionได้
- [ ] Docker CPU service healthyและ real E2Eผ่าน

## Branch/commit/PR breakdown

1. `contract/06-risk-evidence-route-schema`
2. `feat/06-service-registry-foundation`
3. `feat/06-real-training-dataset`
4. `feat/06-risk-baseline`
5. `feat/06-safety-overrides`
6. `feat/06-rag-ingestion`
7. `feat/06-hybrid-retrieval`
8. `feat/06-route-exposure`
9. `feat/06-evidence-package`
10. `test/06-model-rag-route-evaluation`

## Completion report requirements

สร้าง `docs/handoffs/M06-risk-knowledge.md` พร้อม dataset/source manifests, label method, split, metric table/confusion/calibration/subgroup, model card/version/checksum, thresholds/overrides, RAG sources/collection/evaluation, route formula/hard constraints, latency/resources, rollback stepsและ known limitations

