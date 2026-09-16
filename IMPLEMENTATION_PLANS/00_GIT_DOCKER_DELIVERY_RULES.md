# 00 — Git, Docker and Delivery Rules

เอกสารนี้เป็นกติกากลางบังคับสำหรับทั้ง 8 คน เป้าหมายคือให้ merge งานแบบต่อเนื่องโดยไม่เกิด branch ใหญ่ที่รวมยาก และทำให้ reviewer รันผลลัพธ์เดียวกับผู้พัฒนาได้จาก Docker

## 1. Repository bootstrap — Team Lead ทำครั้งเดียว

โฟลเดอร์ปัจจุบันยังไม่เป็น Git repository เมื่อสร้าง remote project แล้วให้ Team Lead ทำดังนี้ (แทน `<REMOTE_URL>` ด้วย URL จริง):

```bash
git init -b main
git remote add origin <REMOTE_URL>
git add assets IMPLEMENTATION_PLANS
git commit -m "docs: add project assets and implementation plans"
git push -u origin main
```

จากนั้นสร้าง initial scaffold PR แยกจาก `main` ห้าม bootstrap source code ด้วย direct push หลัง commit แรก

### ตั้งค่า repository ก่อนให้ทีมเริ่ม

- Protect `main`: require PR, require branch up-to-date, block force push/deletion
- Required approvals ปกติ 1; security/auth/emergency/policy/model/schema/migration 2
- Dismiss stale approvals เมื่อมี commit ใหม่
- Require status checks: lint, typecheck, unit, contract, integration smoke, image scan, secret scan
- Require conversation resolution
- Require signed commits หากทีมตั้งค่าได้
- ใช้ squash merge เป็นค่าเริ่มต้น; ปิด merge commit ที่ไม่จำเป็น
- ให้ Team Lead และ maintainer เท่านั้นที่แก้ branch protection/release tag
- เพิ่ม CODEOWNERS ตาม folder owner เมื่อทราบ GitHub username จริง

## 2. Branch strategy

ห้ามสร้าง branch คนละอันแล้วทำค้างจนจบทั้ง module เพราะจะ conflict สูง แต่ละคนทำเป็น short-lived vertical slice อายุเป้าหมายไม่เกิน 1–3 วัน

รูปแบบ:

```text
<type>/<module-number>-<short-kebab-description>
```

Type ที่อนุญาต: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `contract`, `infra`

ตัวอย่างตามคน:

```text
feat/01-web-shell
feat/01-trip-planner
feat/02-auth-api
contract/02-public-travel-schema
feat/03-agent-graph
feat/04-weather-adapter
feat/05-route-corridor
feat/06-risk-baseline
feat/07-decision-policy
feat/08-feedback-api
infra/00-compose-observability
```

ห้ามใช้ `dev`, `test`, `new`, `mybranch`, ชื่อคนล้วน ๆ หรืออักษรไทยเป็น branch name

### ขั้นตอนเริ่มงานทุกครั้ง

```bash
git switch main
git pull --ff-only origin main
git status
git switch -c feat/04-weather-adapter
```

ก่อนแก้ไฟล์ให้ `git status` ต้องสะอาด ถ้ามีไฟล์ของงานอื่น ห้าม reset/ลบ ให้คุยเจ้าของหรือแยก worktree

ถ้าต้องทำหลายงานพร้อมกัน ใช้ worktree:

```bash
git worktree add ../sta-04-weather -b feat/04-weather-adapter main
```

อย่าสร้าง branch จาก branch ของคนอื่น เว้นแต่ contract dependency ยังไม่ mergeและ Team Lead อนุมัติ ถ้าจำเป็นให้ระบุ `Depends on #PR` และ rebase หลัง dependency merge

## 3. Commit discipline

ใช้ Conventional Commits:

```text
<type>(<scope>): <imperative summary>
```

Scope: `web`, `api`, `agent`, `external-data`, `integration`, `risk`, `decision`, `recommendation`, `contracts`, `infra`, `docs`

ตัวอย่าง:

```text
feat(external-data): add Open-Meteo forecast adapter
test(external-data): add sanitized forecast contract fixture
feat(integration): intersect hazards with route corridor
fix(decision): preserve AVOID action on official closure
docs(web): document responsive dashboard states
```

กติกา:

- 1 commit = 1 แนวคิดที่ review/ย้อนกลับได้
- ห้าม commit `.env`, secret, API response ที่มี PII, database volume, `node_modules`, `.venv`, model cache หรือ generated screenshot จำนวนมาก
- ห้ามใช้ข้อความ `update`, `fix`, `done`, `final`, `wip` โดยไม่มีรายละเอียด
- migration กับ model/schema code ที่ใช้ migration ควรอยู่ commit/PR เดียวกัน
- generated files commit ได้เมื่อ project กำหนดและต้อง generate จาก source; ห้ามแก้มือ
- ก่อน commit ตรวจ diff เสมอ:

```bash
git status --short
git diff --check
git diff
git add <exact-files>
git diff --cached
git commit -m "feat(web): implement trip planner form"
```

ใช้ `git add <exact-files>` มากกว่า `git add .` เพื่อไม่ดูด secret/งานคนอื่นเข้า commit

## 4. Sync, rebase and push

ก่อน push หรือขอ review:

```bash
git fetch origin
git rebase origin/main
```

แก้ conflict ทีละไฟล์และรัน test ใหม่ ห้ามเลือก `ours/theirs` ทั้ง folder โดยไม่อ่าน เมื่อ rebase branch ที่เคย push แล้ว ใช้:

```bash
git push --force-with-lease
```

ห้ามใช้ `--force` เด็ดขาด สำหรับ push ครั้งแรก:

```bash
git push -u origin feat/04-weather-adapter
```

ตรวจ commit ที่จะเข้า PR:

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

## 5. Pull Request sizing and sequence

เป้าหมาย PR < 400 changed lines ที่ต้อง review (ไม่รวม lock/generated) หากใหญ่กว่าให้แยกตามนี้:

1. contract/schema/ADR
2. implementation core
3. persistence/migration
4. tests/fixtures
5. UI/observability/docs

หนึ่ง feature ใช้หลาย PR ได้ แต่ทุก PR ที่ merge ต้อง build/test ผ่านและไม่ทำให้ `main` พัง ใช้ feature flag เมื่อฟังก์ชันยังไม่พร้อมเปิด

### ลำดับ PR ที่แนะนำสำหรับทุก slice

1. `contract/<module>-...` เมื่อเปลี่ยนขอบเขตการคุยข้าม service
2. producer implementation + contract test
3. consumer integration + degraded/error handling
4. E2E/visual acceptance

### Pull Request ต้องมี

- ชื่อ: `[M04] Add Open-Meteo weather adapter`
- Summary และเหตุผล
- Scope/in-scope/out-of-scope
- Contract/API/DB/env changes
- Real data source และ license/attribution
- วิธีรันด้วย Docker แบบ copy-paste ได้
- test result จริงพร้อม command
- screenshot/video สำหรับ UI หรือ `curl` output ที่ sanitize แล้วสำหรับ API
- failure/degraded behavior ที่ทดสอบ
- security/privacy checklist
- dependency/handoff และ known limitations
- link issue และไฟล์ completion report

ใช้ `PR_TEMPLATE.md` เป็น body ไม่ลบหัวข้อที่ไม่เกี่ยว ให้เขียน `N/A — reason`

## 6. Review and merge rules

Reviewer ตรวจอย่างน้อย:

1. Contract ตรง schema และไม่ breaking โดยไม่ version
2. ไม่มี mock runtime/hard-coded current status
3. มี timeout, retry policy, cancellation, degraded state
4. Source provenance/freshness อยู่ครบ
5. ไม่มี secret/PII ใน code/test/log
6. Test ครอบคลุม success, invalid, timeout, provider error และ boundary
7. Docker build deterministic และ non-root
8. docs/env/migration อัปเดต
9. UI ใช้ keyboard/screen reader และ responsive
10. งาน safety-critical รักษา monotonic safety และ official warning priority

Author ห้าม approve PR ตัวเอง Team Lead เป็นผู้ squash merge หลัง required checks/approval ผ่าน เมื่อ merge แล้ว:

```bash
git switch main
git pull --ff-only origin main
git branch -d feat/04-weather-adapter
git push origin --delete feat/04-weather-adapter
```

GitHub สามารถ auto-delete branch ได้ แต่อย่าลบ branch ก่อน merge

## 7. Contract change protocol

เมื่อจำเป็นต้องเปลี่ยน field/API:

1. เปิด issue/ADR สั้น ๆ ระบุ producer, consumers, compatibility และ rollout
2. สร้าง `contract/...` branch จาก `main`
3. แก้ source schema + sanitized examples + compatibility test
4. ถ้าเพิ่ม optional fieldถือ backward compatible; เปลี่ยนชื่อ/type/required/enums มักเป็น breaking
5. Implement producer ให้อ่าน/เขียนได้ทั้งเก่าและใหม่ในช่วงเปลี่ยน
6. Merge consumer ทุกตัว
7. ลบ compatibility code หลัง deprecation PR แยก

ห้าม merge producer ที่ส่ง contract ใหม่ก่อน consumer พร้อม ถ้าไม่มี backward compatibility

## 8. Docker standard

### Root files

- `compose.yaml`: runtime topology กลาง, ไม่มี host-specific bind path, ไม่มี secret จริง
- `compose.dev.yaml`: hot reload, source mount, host ports และ Mailpit
- `.env.example`: ชื่อ variable + comment เท่านั้น
- แต่ละ service มี `Dockerfile`, `.dockerignore`, healthcheck, README
- root `Makefile` หรือ `justfile` เป็น convenience; คำสั่ง Docker ตรงต้องยังใช้ได้

### Compose profiles

```text
core          postgres redis qdrant keycloak
app           web api agent external-data data-integration risk-knowledge decision-engine recommendation
observability otel-collector prometheus grafana
notifications mailpit (local only)
training      mlflow / offline training job (run on demand)
```

### คำสั่งมาตรฐาน

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d
docker compose ps
docker compose logs -f --tail=200 <service>
docker compose exec api alembic upgrade head
docker compose run --rm risk-knowledge python -m app.cli.index_knowledge
docker compose run --rm risk-knowledge python -m app.cli.verify_model
docker compose down
```

บน PowerShell ให้ใช้ `Copy-Item .env.example .env` แทน `cp` หาก alias ไม่ทำงาน

ห้ามใช้ `docker compose down -v` เป็นคำสั่งปกติ เพราะลบฐานข้อมูล/Qdrant volumes ต้องมี backup และยืนยัน scope ก่อนเท่านั้น

### Dockerfile baseline

- pin base image major/minor และ pin dependenciesใน lockfile
- multi-stage build; final imageไม่มี compiler/cache ที่ไม่จำเป็น
- สร้าง user/group non-root และ `USER app`
- copy lockfile ก่อน source เพื่อใช้ layer cache
- `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`
- frontend production ใช้ Next standalone output
- healthcheck เรียก `/health/live`; readiness ให้ compose/CI ตรวจแยก
- image ไม่มี `.env`, tests secret, `.git`, local database หรือ model download cache

### Compose dependency rule

`depends_on` อย่างเดียวไม่รับประกัน ready ให้ใช้ health condition และ service-level retry/backoff ทุก service ต้อง start ได้เมื่อ dependency ยังไม่พร้อมชั่วคราว และเปลี่ยน readiness เป็น unhealthy แทน crash loopไม่สิ้นสุด

## 9. Environment variables

Root `.env.example` แบ่ง section และระบุ required/optional:

```text
# Runtime
APP_ENV=development
LOG_LEVEL=INFO
CONTRACT_VERSION=1.0.0

# Database/cache
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=smart_travel
POSTGRES_USER=...
POSTGRES_PASSWORD=...
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333

# Auth
KEYCLOAK_BASE_URL=http://keycloak:8080
OIDC_ISSUER=...
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...

# Real providers
OPEN_METEO_BASE_URL=https://api.open-meteo.com
OPEN_METEO_GEOCODING_URL=https://geocoding-api.open-meteo.com
ORS_BASE_URL=https://api.openrouteservice.org
ORS_API_KEY=
AMADEUS_BASE_URL=https://api.amadeus.com
AMADEUS_CLIENT_ID=
AMADEUS_CLIENT_SECRET=
USGS_FEED_URL=https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson
GDACS_BASE_URL=https://www.gdacs.org/gdacsapi
EONET_BASE_URL=https://eonet.gsfc.nasa.gov/api/v3
GTFS_PROVIDER_CONFIG=/app/config/providers.yaml

# LLM
OPENAI_API_KEY=
OPENAI_EXPLAINER_MODEL=

# Internal URLs/timeouts
AGENT_SERVICE_URL=http://agent:8001
EXTERNAL_DATA_SERVICE_URL=http://external-data:8002
DATA_INTEGRATION_SERVICE_URL=http://data-integration:8003
RISK_KNOWLEDGE_SERVICE_URL=http://risk-knowledge:8004
DECISION_SERVICE_URL=http://decision-engine:8005
RECOMMENDATION_SERVICE_URL=http://recommendation:8006
```

ค่า password/client secret จริงต้องสร้างต่อเครื่อง ห้ามใส่ค่าที่ใช้งานได้ใน `.env.example`

## 10. Database migration workflow

แต่ละ service มี Alembic config และ version table แยก schema ห้ามใช้ `create_all()` ใน production startup

```bash
docker compose run --rm api alembic revision --autogenerate -m "add trip revision"
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic downgrade -1
```

ก่อน PR:

- ตรวจ autogenerated migration ด้วยมือ
- ทดสอบ empty DB -> head
- ทดสอบ previous main DB -> head
- ทดสอบ rollback ถ้าปลอดภัย; หาก rollback destructive ให้ใช้ forward-fix และเขียน runbook
- ห้าม rename/drop column ที่ consumer ยังใช้
- index/constraint และ retention cleanup ต้องอยู่ใน migration/job ที่ review ได้

## 11. Test commands required before push

แต่ละ moduleกำหนดเพิ่มได้ แต่ขั้นต่ำ:

```bash
# whole repository wrappers
make lint
make typecheck
make test-unit
make test-contract
make test-integration
make test-e2e
make compose-validate

# frontend
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web test
pnpm --filter web test:e2e

# Python service example
docker compose run --rm external-data uv run ruff check .
docker compose run --rm external-data uv run mypy app
docker compose run --rm external-data uv run pytest -q
```

PR ไม่ควรกล่าวแค่ว่า “tests pass” ต้องระบุ command และสรุปจำนวน pass/fail/skipped โดย skip ต้องมีเหตุผล

## 12. CI pipeline

ทุก PR:

1. secret scan + dependency audit
2. format/lint/typecheck ทั้ง TS/Python
3. validate OpenAPI/JSON Schema + breaking change check
4. unit + contract tests แบบ parallel
5. build Docker images
6. start ephemeral Compose/Testcontainers
7. apply migrations
8. integration smoke ใช้ provider sandboxเฉพาะ non-safety path; scheduled CI ใช้ real provider keys
9. Playwright E2E + axe + visual screenshots
10. container vulnerability scan และ SBOM

Nightly/scheduled:

- real provider canary โดยจำกัด quota
- provider schema drift
- model/RAG golden evaluation
- full E2E degraded scenarios
- backup/restore test ตาม schedule

ห้ามเรียก paid/limited real APIs ทุก unit test เพราะทำให้ flaky/quota หมด; real canary แยกจาก deterministic tests แต่ acceptance demo ต้องใช้ real data

## 13. Release and rollback

- tag รูปแบบ `v0.x.y` สำหรับ MVP; image tag ใช้ semver + git SHA ห้ามใช้ `latest` ใน release
- migration, policy, prompt, model, knowledge collection และ contract มี versionใน release manifest
- deploy canary สำหรับ presentation/logic ที่ไม่ลด safety
- policy threshold ห้าม A/B แบบทำ safety อ่อนลง
- rollback code ต้องตรวจ DB migration compatibility; model/policy rollbackต้องเลือก previous approved versionได้โดย config
- incident: disable affected provider/feature flag, return degraded response, preserve official warning, capture correlation IDs และเขียน postmortem

## 14. Handoff ก่อนจบงานของแต่ละคน

1. Rebase `main`, รัน test และ Compose smoke ใหม่
2. อัปเดต service README, `.env.example`, OpenAPI/schema/migration
3. กรอกไฟล์ตาม `10_WORK_COMPLETION_REPORT_TEMPLATE.md` และเก็บที่ `docs/handoffs/Mxx-<feature>.md`
4. แนบ evidence ใน PR
5. ระบุสิ่งที่ consumer ต้องเปลี่ยน/ค่าที่ต้องตั้ง/known limitations
6. นัด owner downstream ตรวจ contract หากเป็น critical integration
7. หลัง merge ลบ branch และยืนยัน `main` pipeline ผ่าน

