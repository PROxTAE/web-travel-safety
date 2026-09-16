# คนที่ 1 — Web App Implementation Plan

## Mission

สร้าง traveler-facing web application ให้ตรง UI 6 หน้าใน `assets/ui-screens/` เชื่อม Public API จริงของคน 2 แสดงข้อมูลสด/ความสด/แหล่งอ้างอิงครบ รองรับ loading, empty, error, partial/degraded, mobile และ accessibility โดย frontend ห้ามคำนวณ final risk/action เอง

ความสำคัญ: นี่คือจุดรับ input และจุดที่ผู้ใช้ตัดสินใจจากคำแนะนำ หากไม่เสร็จจะทดสอบ end-to-end, consent, emergency และความเข้าใจของ recommendation ไม่ได้ หากแสดงข้อมูลผิดหรือซ่อน degraded status อาจทำให้ผู้ใช้เข้าใจว่าเส้นทางปลอดภัยเกินจริง

## Ownership

แก้ได้โดยตรง:

- `apps/web/**`
- `packages/ts-config/**`
- visual E2E ใน `tests/e2e/web/**`
- frontend section ของ root docs โดยขอ review

ต้องขอ review เพิ่มเมื่อแก้:

- `packages/contracts/**` — คน 2 + consumer owner
- `compose*.yaml`, root `.env.example`, CI — Team Lead
- `assets/**` — ห้ามทับ source; derivative ต้องสร้างใหม่พร้อม script

Dependency:

- คน 2: public OpenAPI, OIDC, REST/SSE
- คน 8: `RecommendationResponse`, feedback/alert subscription
- คน 4/5/6: map/event/route fields ผ่าน API ของคน 2
- ส่งผลให้คน 2 และคน 8 ตรวจ public contract/UX ได้

## Stack ที่ต้องใช้

- Node.js 22.22+, pnpm
- Next.js 16 App Router + React 19 + TypeScript strict
- HeroUI v3 + Tailwind CSS v4
- TanStack Query สำหรับ server state; Zustand ใช้ได้เฉพาะ ephemeral UI state ที่ cross-component จริง ๆ
- React Hook Form + Zod + generated contract types
- MapLibre GL JS สำหรับแผนที่/route/marker; Supercluster เมื่อ marker จำนวนมาก
- Auth.js OIDC เชื่อม Keycloak; HttpOnly secure cookie
- `react-markdown` + `rehype-sanitize` หรือ render structured blocks; ห้าม `dangerouslySetInnerHTML`
- Vitest, Testing Library, MSW (test only), Playwright, axe-core
- Storybook optional แต่หากใช้ต้องไม่กลายเป็น runtime mock mode

## Target folder structure

```text
apps/web/
├─ app/
│  ├─ (auth)/login/page.tsx
│  ├─ (app)/layout.tsx
│  ├─ (app)/dashboard/page.tsx
│  ├─ (app)/trips/new/page.tsx
│  ├─ (app)/trips/[tripId]/page.tsx
│  ├─ (app)/trips/[tripId]/compare/page.tsx
│  ├─ (app)/safety-map/page.tsx
│  ├─ (app)/assistant/[conversationId]/page.tsx
│  ├─ (app)/emergency/page.tsx
│  ├─ api/auth/[...nextauth]/route.ts
│  ├─ error.tsx
│  ├─ loading.tsx
│  └─ layout.tsx
├─ components/
│  ├─ shell/          # sidebar/header/page shell
│  ├─ dashboard/
│  ├─ trip/
│  ├─ map/
│  ├─ assistant/
│  ├─ emergency/
│  ├─ recommendation/
│  └─ ui/             # thin wrappers over HeroUI
├─ features/
│  ├─ auth/
│  ├─ trips/
│  ├─ assessment/
│  ├─ conversations/
│  ├─ safety-map/
│  ├─ emergency/
│  └─ feedback/
├─ lib/
│  ├─ api/generated/  # generated, do not edit
│  ├─ api/client.ts
│  ├─ auth.ts
│  ├─ env.ts
│  ├─ map.ts
│  └─ telemetry.ts
├─ public/assets/     # optimized copies, preserve attribution
├─ styles/
├─ tests/
└─ Dockerfile
```

## UI-to-data mapping

### Shared shell

- Sidebar: Overview, My Trip, Safety Map, Assistant, Emergency; active route ชัดเจน, keyboard navigable
- Header: logo, localized greeting, current weather summary optional, profile menu
- desktop sidebar fixedตามภาพ; tabletเป็น collapsible rail; mobileเป็น bottom nav/drawer
- ใช้ provided logo/mascot/illustrations ผ่าน Next Image; decorative image `alt=""`, meaningful imageมี localized alt
- global data status banner แสดง offline/degraded/last updated โดยไม่บัง emergency action

### `/dashboard`

API calls:

- `GET /api/v1/me`
- `GET /api/v1/trips?status=active&limit=1`
- `GET /api/v1/recommendations/{latest}` หรือ linkจาก trip
- `GET /api/v1/safety/events` เฉพาะ corridor/viewport

Components:

- `WeatherSummaryCard`, `TransportSummaryCard`, `RiskSummaryCard`
- `CurrentTripCard` แสดง origin/destination/departure/arrival/mode
- `RouteRiskMap` วาด route alternatives และ alert overlays จริง
- `RecommendationCard` action code mapเป็นข้อความ/UI token แต่ไม่ derive action
- `EmergencyQuickCard` ไม่ hard-codeเลข; resolveจาก emergency contacts API
- `AssistantTeaser` เปิด conversation และส่ง trip ID

Interactions ตาม `assets/README.md`: card เปิด side sheet, marker เปิด detail, `Use safer route` ไป compare, SOS ไป emergency

### `/trips/new` และ `/trips/[tripId]`

- form fields From, To, Departure, Return, modes, preferences
- location autocomplete debounce 300–500ms, cancel requestเก่า, minimum query length, keyboard selection
- หลังเลือก location แสดง map pinให้ยืนยัน; submitไม่ได้จน `confirmed_by_user=true`
- date validationใช้ timezoneของ trip ไม่ใช้ browser timezoneอย่างเดียว
- `Find safe routes`: POST trip/patch -> POST assessment ด้วย Idempotency-Key -> subscribe SSE
- progress map stageจาก contract เป็น localizedข้อความ
- route cards `Recommended`, `Fastest`, `Lowest risk`; แสดง unavailableเมื่อ provider coverageไม่มี ห้ามสร้าง cardปลอม
- คลิก route ไป `/trips/[id]/compare?candidate=...`

### `/safety-map`

- MapLibre source/layers แยก `weather`, `transport`, `natural_hazards`, `health_safety`
- query API ตาม viewport bbox + selected time (`now`, `+6h`, `+12h`); debounce move end และ cancel queryเดิม
- cluster marker; icon + shape + label, legend Low/Moderate/High/Unknown
- popup/detail sheet: title, severity, area, source, observed/fetched/expires, limitations
- `Avoid area` สร้าง route reassessment โดยส่ง event geometry/id ไม่คำนวณ safe routeบน client
- respect source attributionและ map tile attribution

### `/assistant/[conversationId]`

- recent chatsจาก API, new chat, message history pagination
- POST messageแล้ว subscribe SSE; render progress และ structured recommendation blocks
- quick prompt chips ส่ง textจริงพร้อม trip context ID
- trip context panelแสดง risk/weather/transport/freshness
- `Use live location` ต้องเปิด permission explainer -> browser permission -> POST consent/location; offแล้วหยุด watchทันที
- `Update my trip` ไป compare/confirmก่อน mutate
- markdown sanitize; citation clickเปิด sourceใน new tabด้วย `noopener noreferrer`
- ห้ามแสดง chain-of-thought; แสดง reasons/evidence IDsที่ APIอนุญาตเท่านั้น

### `/emergency`

- SOS button ต้อง pointer/key holdครบ 3 วินาที มี progress, cancelเมื่อปล่อย, แล้วเปิด confirmation dialog
- ขั้น 1 Hold -> 2 Confirm -> 3 Share location (optional/consented) -> 4 Connect help
- emergency contact/nearby APIเรียกตามตำแหน่งและประเทศจริง; label source/effective date
- `tel:` เปิด dialerหลังผู้ใช้เลือก ห้าม auto-call
- `Share location` ขอ permissionและอธิบาย recipient/retention; มี stop sharing
- `Find nearest` ใช้ current coordinateและแสดง map/list source; ถ้า geolocationถูกปฏิเสธให้ผู้ใช้เลือกสถานที่เอง
- emergency profileอ่าน/แก้ผ่าน protected UI; medical noteไม่ใส่ analytics/log
- emergency primary coralเฉพาะ critical actionตามภาพ

### `/trips/[id]/compare`

- overlay original/safer routeบนแผนที่เดียวกัน
- card แสดง duration, distance, stops/transfers, risk, hazard exposure, freshness
- `Apply safer route`: confirm -> POST apply-route -> รอ trip revision/assessment -> toast `Trip updated`
- `Keep original`: ถ้า originalมี MEDIUM/HIGH ต้อง checkboxยอมรับความเสี่ยง; actionยังต้องถูก server validate
- `Notify me about changes`: POST/DELETE alert subscriptionและ consent; optimistic UIเฉพาะเมื่อ rollback errorได้
- route geometry/statsมาจาก API ห้ามคำนวณความปลอดภัยจากสีบน client

## Implementation steps from zero to done

### Phase 0 — Contract and visual inventory

1. อ่าน shared docs, public OpenAPI และ UI 6 ภาพที่ full resolution
2. ทำ checklistทุก visible element/interaction/status; บันทึกใน `apps/web/docs/screen-inventory.md`
3. ตกลงกับคน 2 ว่า generated TS client command, auth callback และ SSE authใช้รูปแบบใด
4. ตรวจ field UIกับ `RecommendationResponse`; หากขาดให้เปิด contract PR ก่อนเขียน workaround
5. ยืนยัน font/licensing, asset dimensions/alpha; optimize WebP/AVIFโดยเก็บ PNGต้นฉบับ

### Phase 1 — Scaffold and design system

1. สร้าง Next.js App Router TypeScript strict ด้วย pnpm
2. ติดตั้ง HeroUI v3/Tailwind 4 ตาม official setup; import Tailwindก่อน HeroUI styles
3. สร้าง CSS variablesจาก design tokens, spacing/radius/shadow/type scale
4. สร้าง `AppShell`, sidebar, header, responsive nav, error/loading boundaries
5. ตั้ง ESLint, Prettier, typecheck, Vitest, Playwright, bundle analyzer
6. ตั้ง runtime env validation: `NEXT_PUBLIC_API_BASE_URL`, map tile URL/tokenเท่านั้น; secretห้าม prefix `NEXT_PUBLIC_`
7. Dockerfile production standalone + non-root + health endpoint

Exit: หน้าเปล่าทุก routeเปิดได้, shellตรงสไตล์, keyboard navผ่าน, Docker container healthy

### Phase 2 — Auth and API foundation

1. Auth.js OIDC -> Keycloak, protected route middleware และ sign-out
2. generated OpenAPI clientมี token/correlation header, timeout, AbortSignal, stable error mapper
3. QueryClient defaults: retryเฉพาะ safe/transient status, stale timesตรง freshness, no retry 4xx
4. `useRunEvents` SSE clientรองรับ reconnect/Last-Event-ID, heartbeat, cancel/unmount
5. สร้าง shared components: `DataFreshness`, `SourceList`, `DegradedBanner`, `RiskBadge`, `ActionBadge`, skeleton/empty/error
6. tests auth redirect, 401 refresh/logout, duplicate submit, aborted request

Exit: loginจริง, profileจริง, API clientไม่ใช้ localStorage token, SSE testผ่าน

### Phase 3 — Trip Planner vertical slice

1. Location search + confirmation map
2. Zod/RHF trip formและ timezone validation
3. create/update trip + assessment + progress SSE
4. route options map/listและ error/degraded states
5. Playwright flowสร้างทริปด้วย real integration environment

Exit: จาก loginถึง route optionsได้โดยไม่มี fixtureใน runtime

### Phase 4 — Dashboard and comparison

1. สร้าง dashboard summaryโดยใช้ latest persisted trip/recommendation
2. วาด map sources/layersและ side sheets
3. compare route visual/metrics
4. apply route, risk acceptanceและ alert subscription
5. verify trip revisionเปลี่ยนใน DBผ่าน API

Exit: UI flowภาพ 01 -> 06 -> updated dashboardใช้งานจริง

### Phase 5 — Safety Map

1. viewport/time/layer query stateใน URLเพื่อ share/reloadได้
2. clustering, legend, popup/detailและ freshness
3. avoid-area -> new assessment -> compare
4. test 0 events, thousands markers, provider unavailable, stale event

Exit: map interactiveและไม่ใช้ static imageเป็น data map

### Phase 6 — Assistant

1. conversation list/history/new chat
2. message submit/progress/final recommendation blocks
3. quick prompts/citations/trip context
4. live location consent/watch/stop
5. sanitize/adversarial content tests

Exit: follow-upใช้ conversationเดิมและ current-dataคำถาม refreshตาม server

### Phase 7 — Emergency

1. hold interaction accessibleทั้ง pointer/keyboard
2. confirm/permission/share/connect state machine
3. verified contacts, nearby resultsและ manual location fallback
4. emergency profile formsที่ไม่ leak PII
5. test denied permission, unavailable directory, expired contact, offline

Exit: ไม่มี call/shareก่อน confirmation/consentและเบอร์ไม่ hard-code

### Phase 8 — Responsive, a11y, performance and visual QA

1. เทียบ screenshotที่ viewport 1672×941กับ referenceทีละหน้า; ตรวจ hierarchy/layout/color/asset placement
2. ปรับ 1440/1024/768/390; mobile map/detailเป็น full-screen sheet/drawer
3. axeไม่มี critical/serious violation, keyboard pathครบ, focus visible, reduced motion
4. Next Image sizes/lazy loading; dynamic import MapLibre; bundle budgetและ Core Web Vitals
5. test Thai/English, long place name, large text 200%, UTC/date boundary

### Phase 9 — Final verification and handoff

1. รัน lint/type/unit/component/E2E/visual/a11yใน production container
2. รัน full stack real API scenariosใน runbookและเก็บ screenshotพร้อม request ID/freshness
3. ตรวจ DevToolsว่าไม่มี secret/PIIและไม่มี requestตรง internal services/provider
4. อัปเดต README/env/route mapและ completion report
5. rebase, PR review, แก้ทุก comment, rerun checks

## Required tests

| Level | Must cover |
| --- | --- |
| Unit | mapper enums, date/timezone, risk/action label, permission state, SSE parser |
| Component | form errors, cards, degraded banner, sources, SOS hold, route consent |
| Contract | generated client validate success/error/SSE payload |
| E2E | login, create trip, assessment progress, recommendation, compare/apply, follow-up, emergency, feedback |
| Accessibility | axeทุก page, tab order, dialog focus trap, screen-reader labels, non-color risk |
| Visual | 6 desktop reference viewports + mobile baselines; no accidental overlap/cutoff |
| Failure | 401, 403, 429, timeout, SSE disconnect, partial provider, stale data, location denied |

MSW/fixtureใช้เฉพาะ test process; build productionต้องไม่มี `NEXT_PUBLIC_USE_MOCKS`, mock service workerหรือ sample payload route

## Acceptance checklist

- [ ] 6 หน้าตรง referenceในระดับ layout/visual language และ interactionตาม `assets/README.md`
- [ ] ทุกค่าปัจจุบันมาจาก APIจริงและมี freshness/sourceเปิดดูได้
- [ ] ไม่มี business ruleสรุป final safetyใน browser
- [ ] map/markers/routesเป็น live interactive layers
- [ ] old requestถูก cancelเมื่อ userแก้ route
- [ ] duplicate submitถูกป้องกันและ mutation idempotent
- [ ] loading/empty/error/degraded/partial/offlineครบทุก page
- [ ] auth/token/location/medical infoปลอดภัย
- [ ] responsive + WCAG 2.1 AA
- [ ] production Docker imageรัน non-rootและ E2Eผ่าน

## Branch/commit/PR breakdown

1. `feat/01-web-shell` — commits: scaffold, tokens/assets, shell/responsive; PR visual shell
2. `feat/01-auth-api-client` — OIDC, generated client, SSE, shared states
3. `feat/01-trip-planner` — form/geocode/map/assessment/routes
4. `feat/01-dashboard` — summary/map/recommendation/emergency cards
5. `feat/01-route-comparison` — compare/apply/consent/notifications
6. `feat/01-safety-map` — layers/timeline/detail/avoid
7. `feat/01-assistant` — conversations/stream/location/citations
8. `feat/01-emergency-center` — SOS state machine/contacts/profile
9. `test/01-visual-a11y-e2e` — viewports/axe/full flows

ทุก PR ใช้ `PR_TEMPLATE.md` แนบ before/after screenshot, command/result และ request IDs จาก real integration เมื่อเกี่ยวข้อง

## Completion report requirements

สร้าง `docs/handoffs/M01-web-app.md` จาก template และระบุเพิ่ม:

- route/component tree และ server/client component decision
- API hooks/query keys/cache policy
- screen-by-screen feature/state matrix
- asset optimization changesและ license/attribution
- visual diff/a11y/performance evidence
- browser/deviceที่ทดสอบ
- known pixel/interaction gaps พร้อมเหตุผล
- วิธีตรวจว่า production buildไม่มี mock worker/sample data

