# Smart Travel Assistant — Team Implementation Pack

ชุดเอกสารนี้แปลงโจทย์ `DL-07: Agentic AI System II` ให้เป็นแผนลงมือทำสำหรับทีม 8 คน โดยยึด UI ใน `assets/ui-screens/` เป็น visual acceptance target และยึด flow จาก GitHub ต้นทางเป็น functional baseline

อัปเดตสถาปัตยกรรมและแหล่งอ้างอิงล่าสุด: 2026-09-17

## กติกาสำคัญที่สุด

1. ห้าม commit หรือ push ตรงเข้า `main`
2. ทุกงานต้องผ่าน Pull Request และ reviewer อย่างน้อย 1 คน; งานเกี่ยวกับ decision policy, emergency, auth, schema และ migration ต้องมี reviewer 2 คนรวม Team Lead
3. Runtime, demo และ acceptance test ต้องใช้ API/ฐานข้อมูล/โมเดลจริง ห้ามมี hard-coded mock response หรือ fallback ที่ปลอมว่าเป็นข้อมูลปัจจุบัน
4. Unit/contract test ใช้ stub หรือ fixture ได้เฉพาะ fixture ที่ลดข้อมูลส่วนตัวและบันทึกจาก response จริง พร้อม `source`, `captured_at`, `schema_version`; ห้ามนำ fixture ไปแสดงใน production/demo
5. ข้อมูลขาดหรือ provider ล่มต้องแสดง `degraded` / `unavailable` พร้อมเวลาอัปเดต ห้ามแต่งข้อมูลเติม
6. LLM มีหน้าที่อธิบายผลเท่านั้น ห้ามลดระดับ official warning หรือตัดสิน safety action เอง
7. ทุก service ต้องมี `/health/live`, `/health/ready`, structured log, correlation ID, timeout และ test
8. ก่อนเริ่มงานแต่ละคนต้องอ่าน `00_SHARED_PROJECT_CONTEXT.md`, `00_API_AND_DATA_CONTRACTS.md` และ `00_GIT_DOCKER_DELIVERY_RULES.md` ให้ครบ แล้วจึงอ่านแผนของตน

## ไฟล์ที่ต้องอ่าน

| ลำดับ | ไฟล์ | เจ้าของหลัก | ผลลัพธ์ |
| --- | --- | --- | --- |
| 00 | `00_SHARED_PROJECT_CONTEXT.md` | ทุกคน | เป้าหมาย, architecture, folder ownership, UI และข้อมูลกลาง |
| 00 | `00_API_AND_DATA_CONTRACTS.md` | ทุกคน | API contract, enum, schema, DB ownership และ SSE |
| 00 | `00_GIT_DOCKER_DELIVERY_RULES.md` | ทุกคน | Git flow, Docker, CI, PR, review และ handoff |
| 01 | `01_WEB_APP_IMPLEMENTATION.md` | คนที่ 1 | Web UI ครบ 6 หน้าและเชื่อม API จริง |
| 02 | `02_API_BACKEND_IMPLEMENTATION.md` | คนที่ 2 | Public API, auth, persistence และ request orchestration |
| 03 | `03_TRAVEL_AI_AGENT_IMPLEMENTATION.md` | คนที่ 3 | LangGraph orchestration, tools และ conversation state |
| 04 | `04_EXTERNAL_DATA_SERVICES_IMPLEMENTATION.md` | คนที่ 4 | Weather, transport, disaster, geocoding adapters |
| 05 | `05_DATA_INTEGRATION_IMPLEMENTATION.md` | คนที่ 5 | Normalize, deduplicate, geospatial corridor และ snapshot |
| 06 | `06_RISK_KNOWLEDGE_SERVICES_IMPLEMENTATION.md` | คนที่ 6 | Local risk model, RAG และ route analysis |
| 07 | `07_DECISION_LLM_ENGINE_IMPLEMENTATION.md` | คนที่ 7 | Deterministic action + grounded LLM explanation |
| 08 | `08_RECOMMENDATION_FEEDBACK_IMPLEMENTATION.md` | คนที่ 8 | Final response, alerts, feedback และ notification |
| 09 | `09_INTEGRATION_ACCEPTANCE_RUNBOOK.md` | Team Lead + ทุกคน | วิธีรวมระบบและเกณฑ์ตรวจรับ end-to-end |
| 10 | `10_WORK_COMPLETION_REPORT_TEMPLATE.md` | ทุกคน | แบบรายงานสิ่งที่ทำ ปัญหา หลักฐาน และ handoff |
| PR | `PR_TEMPLATE.md` | ทุกคน | เนื้อหาที่ต้องกรอกใน Pull Request |

## บทบาทและ dependency หลัก

| คน | Module | รับข้อมูลจาก | ส่งข้อมูลให้ | ถ้าไม่เสร็จจะเกิดอะไร |
| --- | --- | --- | --- | --- |
| 1 | Web App | 2, 8 | ผู้ใช้, 2 | ไม่มีหน้าจอรับ input/แสดงผลและตรวจ UI ไม่ได้ |
| 2 | API & Backend | 1, 8 | 3, 1 | ทุก module ไม่มี public trust boundary และ auth |
| 3 | Travel AI Agent | 2 | 4, 5, 6, 7, 8 | ไม่มี orchestration และ follow-up context |
| 4 | External Data | provider จริง | 3, 5 | ไม่มีข้อมูลสด; ระบบประเมินความเสี่ยงไม่ได้ |
| 5 | Data Integration | 4 | 3, 6, 7 | เวลา/หน่วย/พิกัดไม่ตรงและโมเดลรับข้อมูลไม่ได้ |
| 6 | Risk & Knowledge | 5, 4 | 3, 7 | ไม่มี risk score, หลักฐาน RAG หรือเส้นทางปลอดภัย |
| 7 | Decision & LLM | 5, 6 | 3, 8 | ไม่มี action ที่ deterministic และคำอธิบายที่ตรวจสอบได้ |
| 8 | Recommendation & Feedback | 7 | 2, 1, 3 | ไม่มี response สุดท้าย, live alert และ feedback loop |

## ลำดับ integration ที่ลดการรอกัน

```text
Contract baseline (Team Lead + คน 2)
       ├── คน 1 ทำ UI shell/visual states
       ├── คน 4 ทำ real provider adapters
       ├── คน 5 ทำ canonical pipeline
       └── คน 6 เตรียม model/RAG/route

คน 4 -> คน 5 -> คน 6 -> คน 7 -> คน 8
                    \             /
                     -> คน 3 ----
                           |
                         คน 2
                           |
                         คน 1
```

แต่ละคนเริ่มพร้อมกันได้จาก contract ที่ lock แล้ว การ merge ให้ใช้ slice เล็ก ๆ ไม่รอทำทั้ง module เสร็จใน PR เดียว

## Definition of Done ระดับทีม

- `docker compose up --build` แล้ว service ที่บังคับทั้งหมดขึ้นเป็น healthy
- สร้างบัญชี/ล็อกอิน, สร้างทริป, ดึงข้อมูลจริง, ประเมิน route, สร้าง recommendation และเปิดดู source ได้ครบ
- UI 6 หน้าทำงานตาม `assets/README.md` และ visual regression ผ่าน threshold ที่กำหนดใน runbook
- ไม่พบ mock payload, hard-coded current status, API key หรือ PII ใน repository/log
- มี migration, seed เฉพาะ reference data ที่ตรวจสอบแหล่งที่มาได้, และฐานข้อมูล persist ผ่าน Docker volume
- มี unit, contract, integration และ end-to-end tests ตามแผนของแต่ละ module
- ทุกผลลัพธ์มี `request_id`, provenance, freshness, version และ degraded status
- คนทำงานกรอก `10_WORK_COMPLETION_REPORT_TEMPLATE.md` และแนบใน PR ก่อนขอ merge

