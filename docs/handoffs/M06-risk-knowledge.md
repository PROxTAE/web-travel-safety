# [M06] Risk and Knowledge Services — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M06 Risk & Knowledge — คน 6 |
| Branch | `feat/06-risk-knowledge` → main (squash title `[M06] Add local risk model, safety overrides, disaster RAG and route ranking`) |
| Base SHA | `d66b24a` (M05 merge) |
| Date | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 5 (feature schema), คน 7 (evidence package), Team Lead (model acceptance, knowledge sources) |
| Contract version | 1.0.0 (+ `WeatherForecastPoint.probe`, `IntegratedTravelContext.features_delayed`, `EvidencePackageRequest.country_code`; all optional) |
| Docker image | `sta/risk-knowledge:1.0.0` (embedding model pre-baked, non-root, CPU limit 2 / 3 GB) |
| Versions | thresholds 1.0.0 · overrides 1.0.0 · model_acceptance 1.0.0 · label_policy 1.0.0 · ranking 1.0.0 · knowledge collection 2026.09.1 · embedding `intfloat/multilingual-e5-small` |

## 2. Executive summary

- **Part A (risk model):** สร้าง pipeline เทรนจากข้อมูลจริง 7,296 route-days (24 corridors, 2025-11 → 2026-08): features มาจาก
  *พยากรณ์ล่วงหน้า 1 วัน* (Open-Meteo Previous Runs) + USGS + GDACS ที่รู้ก่อนออกเดินทาง, label มาจาก *ค่าจริงที่เกิดขึ้น*
  (Open-Meteo Archive/ERA5, GDACS Orange/Red, USGS M≥5.5) — ไม่ circular, ไม่ synthetic. Features ผ่าน data-integration
  API เดียวกับ runtime (online/offline parity โดยการออกแบบ)
- ผลลัพธ์ที่ซื่อสัตย์: logistic regression (calibrated) ได้ ROC-AUC 0.79, PR-AUC 0.293 (ดีกว่า rule baseline 0.165)
  แต่ HIGH recall 0.65 (CI95 0.54–0.74) ต่ำกว่าเกณฑ์ที่ตกลงไว้ล่วงหน้า 0.80 และ PR-AUC < 0.35 → **โมเดล 1.0.0 เป็น CANDIDATE
  ไม่ถูก promote**; service ใช้ deterministic rule baseline (rule-1.0.0) + safety overrides แทน และรายงานเป็น degraded/limitation
- **Safety overrides** (YAML v1.0.0) ยกระดับได้อย่างเดียว: official closure → HIGH, official SEVERE/EXTREME → HIGH,
  extreme weather → HIGH, M≥6 → HIGH, coverage <0.5 หรือ missing critical → ไม่ LOW (UNKNOWN). พิสูจน์ด้วย Hypothesis property tests
- **Part B (RAG):** 7 เอกสาร official/UN (FEMA Ready.gov ×6, WHO) ingest จริง 56 chunks, Qdrant versioned collection + alias,
  hybrid dense(e5)+BM25 RRF, filter expiry/geography/hazard/language, citation resolve ได้ทุก passage; golden recall@3 = 1.0;
  Thai query → English official passage score 0.82
- **Part C (routes):** hard closure constraint, cost v1.0.0, labels ซื่อสัตย์, deterministic ties, trade-off text ไม่ใช้ LLM;
  พบและแก้ bug สำคัญ: override ที่ยกระดับต้อง floor score ด้วย มิฉะนั้น ranking อาจเลือกเส้นทางที่มีประกาศทางการ
- ส่งต่อ `EvidencePackage` ให้คน 7 ผ่าน `POST /internal/v1/evidence/package`; 22 tests + mypy strict ผ่าน

## 3. Acceptance checklist

- [x] local risk model เทรนจากข้อมูลจริงที่มี manifest/provenance — `model-cards/1.0.0/dataset.manifest.json` (sha256, sources, windows)
- [x] time/location split และ leakage checks — split by `region` ∪ `date ≥ 2026-05-01`; alerts ที่ประกาศหลัง departure ไม่เข้า features
- [x] HIGH-risk false negatives และ calibration รายงานชัด — FNR 0.353, ECE 0.005, Brier 0.019, confusion matrix ใน model card
- [x] official closure/warning override model ได้ — `config/overrides.yaml`, `test_official_*`
- [x] RAG ใช้ approved/current documents และ citation resolve ได้ทุก passage — `knowledge/sources.yaml`, `resolve_citation`
- [x] expired/wrong-region/low-confidence evidence ไม่ถูกใช้ — `test_retrieval_filters_expired_wrong_region_and_hazard`
- [x] route rank ตรวจ full corridor/time และ hard constraints — ใช้ exposure จากคน 5; closed ⇒ unusable
- [x] outputs มี model/threshold/collection/feature versions — `RiskAssessment.model`, `EvidencePackage.knowledge_collection_version`
- [x] rollback model/knowledge collection ได้ — `verify_model --rollback`, `index_knowledge --rollback`
- [ ] **Model promoted to ACTIVE** — not completed: acceptance criteria not met (see §13). Serving = rule baseline (documented degraded state)
- [ ] Docker CPU service healthy + real E2E — pending compose run (unit/API tests pass in-process)

## 4. What was implemented

| Feature | Behavior now | Entry point | Status |
| --- | --- | --- | --- |
| Dataset builder | real forecast/actual/official data → data-integration snapshots → Parquet + manifest | `training/build_dataset.py` | Complete (7,296 rows) |
| Trainer | rule baseline vs calibrated LogReg vs HGB; OOF-derived operating points; time+geo split; model card; promotion gate | `training/train.py` | Complete (CANDIDATE) |
| Model registry | ACTIVE-only loading, checksum + feature-schema verification, rollback | `app/risk/model_loader.py`, `app/cli/verify_model.py` | Complete |
| Inference | model or rule baseline → thresholds → overrides (raise-only, score floor) → evidence sufficiency → DELAY probe | `app/risk/inference.py` | Complete |
| Knowledge ingest | allowlisted download, HTML→sections, list-preserving chunks, checksum | `app/knowledge/ingest.py` | Complete |
| Index/retrieval | versioned Qdrant + alias, BM25, RRF, filters, diversity, citations | `app/knowledge/index.py` | Complete |
| Route ranking | constraints, cost, labels, trade-offs | `app/routes/ranking.py` | Complete |
| Evidence package | assess + retrieve + rank + facts + limitations | `POST /internal/v1/evidence/package` | Complete |

Flow: `snapshot → assess_all (per route) → rank_routes → hazards_in_scope → build_query → retrieve → EvidencePackage`.
Degraded: model unavailable ⇒ `MODEL_UNAVAILABLE` + rule baseline; no evidence ⇒ `NO_RELIABLE_KNOWLEDGE_EVIDENCE`; gate BLOCK ⇒ 422.

Not implemented: cross-encoder reranker (flag exists, default off — latency budget); Thai-language official documents
(no stable allowlistable URLs yet); MLflow UI (metadata is in manifest JSON + `knowledge.model_versions` table).

## 5. Design decisions

- Labels from *observed* outcomes vs features from *day-1 forecasts* avoids circular weak supervision.
- Training data flows through the real data-integration service (HTTP) instead of importing its code: guarantees the
  same feature code path (05 plan Phase 6) without a shared package.
- Stacked `rule_score` feature (computed identically online) lets the model learn when to trust the rule.
- Operating points derived on out-of-fold training predictions only; acceptance thresholds were fixed before training.
- Score floor on overrides so ranking cost (score-based) cannot prefer a route under official restriction.
- Hashing embedder exists for tests only and is refused outside `APP_ENV=test`.

## 6. API

| Method/path | In | Out | Consumer |
| --- | --- | --- | --- |
| `POST /internal/v1/risk/assess` | `{snapshot, route_ids?}` | `RiskAssessment[]` | agent |
| `POST /internal/v1/knowledge/retrieve` | hazards/country/risk/locale/question | `RetrievedEvidence[]` + limitations | agent/assistant |
| `POST /internal/v1/routes/evaluate` | `{snapshot, preferences}` | ranked routes + `materially_safer_available` | agent |
| `POST /internal/v1/evidence/package` | `EvidencePackageRequest` | `EvidencePackage` | agent → decision |
| `GET /internal/v1/models/current` | — | ACTIVE metadata or `UNAVAILABLE` + fallback | ops/runbook |
| `GET /internal/v1/knowledge/status` | — | collection version/points/model | ops/runbook |

## 7. Storage

- PostgreSQL `knowledge` (migration `0001_knowledge_baseline`): model_versions, documents, chunks, risk_assessments, route_evaluations
- Qdrant: `sta_knowledge_v2026_09_1` + alias `sta_knowledge_active` (rollback = alias repoint)
- Artifacts volume: `artifacts/<version>/{model.joblib,manifest.json,thresholds.yaml,MODEL_CARD.md}`, `artifacts/current` only when ACTIVE

## 8. Real data

| Source | Use | License |
| --- | --- | --- |
| Open-Meteo Previous Runs API | day-1 forecast features (4 corridor points/route, hourly) | CC BY 4.0 |
| Open-Meteo Archive API (ERA5) | observed labels (precip ≥30 mm/h, gust ≥89 km/h, severe WMO codes) | CC BY 4.0 |
| GDACS Orange/Red list | official-alert labels + pre-departure features | GDACS terms |
| USGS FDSN M≥4.0 | quake features (24 h prior) + M≥5.5 labels | public domain |
| FEMA Ready.gov (6 pages), WHO floods | knowledge base | public domain / WHO terms |

Runtime has no mock data; the test suite uses an in-memory Qdrant and a hashing embedder (test-only).

## 10. Tests

| Type | Command | Result |
| --- | --- | --- |
| Lint/type | `uv run ruff check . && uv run mypy app` | pass (22 files) |
| Unit/property/API | `uv run pytest -q` | 22 passed (Hypothesis: 150 monotonicity + 60 low-coverage cases) |
| Knowledge golden | `python -m app.cli.index_knowledge --no-release` | recall@3 = 1.0 (5 queries, 56 points) |
| Training | `python -m training.train` | see model card (CANDIDATE) |

## 12. Safety review

- [x] official warnings cannot be lowered by model (overrides raise-only; property-tested)
- [x] retrieved text is untrusted data (never used as instruction; injection passage test)
- [x] no PII; training rows hold route ids and weather only
- [x] fallback (rule baseline) is explicit in `model.name`, reason codes and degraded services

## 13. Problems encountered

| Problem | Evidence | Resolution / status |
| --- | --- | --- |
| Pre-set thresholds 0.55/0.25 gave 2.6% recall | calibrated p for 2% base rate rarely > 0.55 | operating points derived on OOF train predictions (documented in `thresholds.yaml` of the artifact) |
| Model below acceptance | recall 0.65 < 0.80; PR-AUC 0.29 < 0.35; rule recall 0.85 at its own operating point | **not promoted**; rule baseline serves; needs more positives / better features (see §15) |
| Ranking preferred route under official alert | override raised level but not score | score floored at level threshold (`inference.py`) |
| Polars schema inference on mixed None/float | build crashed at frame creation | `infer_schema_length=None` |
| Alias-less candidate evaluation | recall 0.0 in first index run | `load_collection(name)` for candidates |
| Docker Scout: `transformers` 4.57 (3 HIGH) and `lxml` 5.4 (1 HIGH) in the image | fixable CVEs | sentence-transformers 5.7 / transformers 5.17 / lxml 6.1; same `multilingual-e5-small` weights, 22 unit tests + live `knowledge/retrieve` OK (2.6 s cold load) |

## 15. Known limitations

| Limitation | Impact | Next step | Owner |
| --- | --- | --- | --- |
| Model CANDIDATE only | risk = rule baseline + overrides (transparent but coarser) | +12 months data, add ORS road exposure, human-reviewed labels, re-evaluate against the same acceptance file | คน 6 |
| 80% recall ⇒ ~30% flag rate | unusable for AVOID at that operating point | discuss objective with อาจารย์ before v1.1 (precision-constrained objective would be a new approved acceptance version) | Team Lead |
| No Thai official documents | Thai users get English passages | register DDPM/TMD PDFs once stable URLs approved | Team Lead |
| 4 corridor points in training vs 12 online | mild distribution shift in max-features | resample with 12 points when quota allows | คน 6 |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 7 | `EvidencePackage` (assessments with `time_dependent/later_window_*`, ranked routes with `usable/labels/trade_offs`, evidence with ids, facts, limitations, degraded) | consistency gate on request/snapshot/route ids; never lower `risk_level`; treat `MODEL_UNAVAILABLE` as a limitation |
| คน 3 | tool `risk_knowledge.build_evidence_package@1` | pass `country_code` of destination, locale, question (untrusted) |
| คน 8 | `RetrievedEvidence` for emergency instructions | show `source_url/section`, never LLM-invented steps |
| Team Lead | model card + acceptance failures | decide on acceptance v1.1 objective; approve Thai sources |

## 18. Rollback

`python -m app.cli.verify_model --rollback <version>`; `python -m app.cli.index_knowledge --rollback <collection>`;
thresholds/overrides are files → git revert + restart.

ผู้จัดทำ: คน 6 (simulated) · วันที่: 2026-09-17
