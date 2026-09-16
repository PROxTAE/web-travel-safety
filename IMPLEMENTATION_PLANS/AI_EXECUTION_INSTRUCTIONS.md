# วิธีส่งแผนให้ AI Coding Agent

ใช้ข้อความนี้พร้อมแนบไฟล์ shared 3 ไฟล์และไฟล์ของคนที่รับผิดชอบ ไม่ต้องให้ AIตีความจากภาพเพียงอย่างเดียว

```text
Implement the assigned module end-to-end in the current repository.

Authoritative files, in order:
1. IMPLEMENTATION_PLANS/00_SHARED_PROJECT_CONTEXT.md
2. IMPLEMENTATION_PLANS/00_API_AND_DATA_CONTRACTS.md
3. IMPLEMENTATION_PLANS/00_GIT_DOCKER_DELIVERY_RULES.md
4. IMPLEMENTATION_PLANS/<ASSIGNED_MODULE_FILE>.md
5. IMPLEMENTATION_PLANS/09_INTEGRATION_ACCEPTANCE_RUNBOOK.md
6. assets/README.md and assets/ui-screens/* when the module affects UI

Follow the ownership boundaries. Do not write to or merge main. Start from an up-to-date main branch and create the exact short-lived branch described in the module plan. Inspect the existing repository and preserve unrelated work. If an existing contract conflicts with the plan, do not silently fork it: propose or implement a versioned contract change with producer and consumer tests.

Implement production runtime behavior with real APIs, a real persistent database, migrations, Docker, health/readiness, observability, error/degraded states and tests. Do not add runtime mock data, hard-coded current conditions, fake success responses, or secrets. Sanitized fixtures captured from real providers are allowed only inside deterministic automated tests with provenance metadata. If a credential or provider coverage is unavailable, return and display an explicit unavailable/degraded state.

Work through the plan phase by phase. After each coherent slice, run the relevant checks and make a Conventional Commit. Before asking for review, rebase on origin/main, run all required module and integration checks, fill docs/handoffs/MXX-<feature>.md from IMPLEMENTATION_PLANS/10_WORK_COMPLETION_REPORT_TEMPLATE.md, and prepare a Pull Request body from IMPLEMENTATION_PLANS/PR_TEMPLATE.md with exact commands and evidence.

Do not claim completion while required checks fail or required behavior is missing. Report blockers with concrete evidence, continue all safe independent work, and leave the repository in a buildable state.
```

แทน `<ASSIGNED_MODULE_FILE>` ด้วยไฟล์เช่น `01_WEB_APP_IMPLEMENTATION.md` ผู้ใช้สามารถสั่งสั้น ๆ ต่อท้ายว่า “ดำเนินการตามเอกสารนี้ให้ครบจนพร้อมเปิด PR” ได้

AIต้องไม่ทำ actionภายนอกขอบเขต เช่น merge PR, เปลี่ยน branch protection, ซื้อ API plan, ส่ง notificationไปผู้ใช้จริง หรือใช้ secret production จนกว่าจะได้รับสิทธิ์ชัดเจน

