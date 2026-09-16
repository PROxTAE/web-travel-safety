# 10 — Work Completion / Improvement Report Template

ทุกคน copyไฟล์นี้เป็น `docs/handoffs/MXX-<feature>.md` แล้วกรอกก่อนขอ final review ห้ามเขียนเพียง “ทำเสร็จแล้ว” รายงานต้องทำให้คนถัดไป/AIเข้าใจสิ่งที่มีจริง, ปัญหาที่พบ, วิธีรัน/ตรวจ และสิ่งที่ยังไม่เสร็จ

---

# [MXX] Feature/Module Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | |
| Issue/PR | |
| Branch | |
| Base/final commit SHA | |
| Date/time/timezone | |
| Reviewers | |
| Contract version | |
| Docker image digest/tag | |
| Related model/policy/prompt/collection version | |

## 2. Executive summary

อธิบาย 5–10 บรรทัด:

- ทำอะไรสำเร็จ
- ผู้ใช้/ระบบได้ผลลัพธ์อะไร
- เชื่อมกับ moduleใด
- ข้อจำกัดสำคัญที่สุด
- พร้อม merge/releaseหรือไม่ เพราะอะไร

## 3. Original responsibility and acceptance criteria

คัดลอก checklistจาก Implementation Plan ของตนและ mark:

- [x] completed — evidence/link
- [ ] not completed — reason/owner/target

อธิบาย scopeที่เปลี่ยนจากแผนและ link ADR/approval ห้ามซ่อนงานที่ตัดออก

## 4. What was implemented

### Features

| Feature | Behavior now | Entry point | Status |
| --- | --- | --- | --- |
| | | | Complete/Partial/Flagged |

### Important flows

อธิบาย step-by-stepของ flowจริงตั้งแต่ inputถึง output รวม success/degraded/error เช่น:

```text
Input -> validation -> dependency -> persistence -> output/event
```

### What is explicitly not implemented

- ...

## 5. Actual architecture and code design

### Folder/file map

| Path | Purpose | Important owner/consumer |
| --- | --- | --- |
| | | |

### Main components/classes/functions

| Symbol | Responsibility | Inputs/outputs | Design notes |
| --- | --- | --- | --- |
| | | | |

### Decisions/trade-offs

- Decision:
- Alternatives considered:
- Reason:
- Consequence:
- ADR link:

ระบุหนี้เทคนิคและเหตุผลที่ยอมรับได้

## 6. API, contract and event changes

| Producer | Method/path/event | Request schema | Response schema | Consumer | Compatibility |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

- Generated client command/result:
- Contract lint/breaking check result:
- Deprecation/migration plan:
- Sanitized request/response example location:

## 7. Database, cache and storage changes

### Migrations

| Revision | Schema/table/index | Upgrade | Downgrade/forward fix | Data impact |
| --- | --- | --- | --- | --- |
| | | | | |

- Empty DB -> head result:
- Previous main -> head result:
- Restart persistence result:
- Backup/restore result:
- Retention/cleanup behavior:
- Encryption/access control:

### Redis/Qdrant/artifact changes

- Key/collection/model artifact names:
- TTL/version/checksum:
- Ownership/cleanup/rollback:

## 8. External providers and real data

| Provider/source | Endpoint/capability | Coverage | Credential ref | Freshness/TTL | License/attribution | Last canary |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

- ยืนยัน runtime/demoไม่มี mock/hard-coded current data: [ ]
- Test fixture source/captured_at/redaction/license:
- Unsupported/unavailable capability behavior:
- Schema drift/quota/failover behavior:

## 9. Configuration and Docker

### Environment variables added/changed

| Variable | Required | Secret | Default/example | Used by | Failure if missing |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

### Run commands

```bash
# exact build/start/migrate/index/seed/test commands
```

- Container user:
- Ports/networks/volumes:
- Health/readiness behavior:
- CPU/RAM/disk measured:
- Image size/digest:

## 10. Tests and verification

| Test type | Command | Passed | Failed | Skipped | Evidence |
| --- | --- | ---: | ---: | ---: | --- |
| Lint/type | | | | | |
| Unit | | | | | |
| Contract | | | | | |
| Integration | | | | | |
| E2E | | | | | |
| Security/privacy | | | | | |
| Accessibility/visual (if applicable) | | | | | |
| Load/performance | | | | | |

### Scenarios verified

- Success:
- Invalid input/unauthorized:
- Timeout/429/5xx:
- Stale/partial/conflicting data:
- Cancellation/idempotency/concurrency:
- Restart/rollback:

แนบ request ID/correlation ID/trace IDแบบ sanitize

## 11. UI evidence (if applicable)

| Screen/state/viewport | Reference | Result screenshot | Visual gap |
| --- | --- | --- | --- |
| | | | |

- Keyboard/axe results:
- Thai/English/long text/200% zoom:
- Loading/empty/error/degraded/offline:

## 12. Safety, security and privacy review

- [ ] official warning/closure priority preserved
- [ ] LLM/provider/RAG data treated as untrusted
- [ ] no secret/PII/exact location in code/log/trace/fixture
- [ ] consent/auth/ownership enforced
- [ ] timeout/retry/cancel/idempotency bounded
- [ ] source/freshness/quality/version retained
- [ ] fallback/degraded behavior does not invent data
- [ ] dependency/image/secret scans passed

อธิบาย findingที่พบและวิธีแก้/accept:

## 13. Problems encountered and resolutions

| Problem | Root cause | Evidence | Resolution/workaround | Remaining risk |
| --- | --- | --- | --- | --- |
| | | | | |

อย่าเขียนแค่ว่า “APIมีปัญหา” ให้ระบุ status/error/time/provider/traceและสาเหตุที่พิสูจน์ได้

## 14. Performance and operational behavior

| Metric | Target | Actual p50/p95/max | Test condition | Pass |
| --- | --- | --- | --- | --- |
| | | | | |

- Metrics/dashboard/alerts added:
- Log fields/redaction verified:
- Circuit/rate/quota/cache behavior:
- Incident disable/rollback steps:

## 15. Known limitations and technical debt

| Limitation/debt | User/safety impact | Workaround | Owner | Priority | Follow-up issue |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

## 16. Handoff to other members

| Recipient/module | What is ready | What they must change/do | Contract/config | Blocking? |
| --- | --- | --- | --- | --- |
| | | | | |

ระบุ exact branch/PR/version/path ห้ามใช้ข้อความกำกวมว่า “เชื่อมต่อได้แล้ว”

## 17. Commit and PR inventory

```text
<sha> <conventional commit message>
```

- PR review comments resolved:
- Required checks status:
- Rebased on main SHA:
- Squash title proposed:

## 18. Rollback and recovery

1. Feature flag/provider disable:
2. Application rollback image/tag:
3. Migration downgradeหรือ forward-fix:
4. Model/policy/prompt/knowledge rollback:
5. Data/cache cleanupที่ปลอดภัย:
6. Verificationหลัง rollback:

ห้ามเสนอการลบ volume/ฐานข้อมูลโดยไม่มี backupและ exact target

## 19. Final declaration

- [ ] งานใน scopeครบตามหลักฐาน
- [ ] ไม่มี required workที่ซ่อนอยู่
- [ ] documentation/env/contracts/migrationsอัปเดต
- [ ] downstream ownersได้รับ handoff
- [ ] พร้อม merge
- [ ] พร้อม release (ถ้าไม่พร้อมอธิบาย)

ผู้จัดทำ:

ผู้ review:

วันที่:

