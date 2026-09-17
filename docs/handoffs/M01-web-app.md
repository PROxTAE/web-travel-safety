# [M01] Web App — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M01 Web App — คน 1 |
| Branch | `feat/01-web` → main (`[M01] Add traveler web app: 6 screens, OIDC, SSE, maps, emergency`) |
| Base SHA | `0edf678` (M02 merge) |
| Date | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 2 (public contract/SSE/auth), คน 8 (recommendation UX), Team Lead (compose/env) |
| Versions | Next.js 16.3.5, React 19.3, HeroUI 3.2.5, Tailwind 4.3, MapLibre 6.10, Auth.js 5.0.0-beta.32, contract 1.0.0 |

## 2. Executive summary

- 6 หน้าครบตาม `assets/ui-screens` (layout 3 คอลัมน์ + sidebar rail + header greeting, โทนมิ้นต์–เขียว–ส้ม, mascot/illustrations
  จาก asset pack ผ่าน `next/image`) responsive: desktop rail → mobile bottom nav, side sheets/drawers
- ทุกค่ามาจาก Public API จริงผ่าน same-origin proxy (`/api/backend/*`) ที่แนบ bearer จาก HttpOnly cookie; browser ไม่เห็น token,
  ไม่มี request ตรงไป internal service/provider, ไม่มี `NEXT_PUBLIC_USE_MOCKS`/MSW ใน runtime (`verify:no-mocks` ใน Docker build)
- Auth.js v5 + Keycloak (code+PKCE, refresh rotation, RP-initiated logout); `proxy.ts` guard → `/login?next=`; API paths ได้ 401 JSON
- Trip planner: geocode autocomplete (debounce 350 ms, abort เก่า, keyboard), ยืนยัน pin บนแผนที่ก่อน submit, เวลาใน timezone ของทริป,
  POST assessment ด้วย `Idempotency-Key` ต่อการกด, SSE progress (`EventSource` + reducer dedupe), route cards จาก label ของ server เท่านั้น
- Dashboard/compare/safety-map/assistant/emergency ตาม interaction flow ใน `assets/README.md` (ดู `apps/web/docs/screen-inventory.md`)
- Emergency: hold 3 s (pointer+keyboard, ปล่อยก่อนยกเลิก) → confirm → share (consent + permission, stop ทันที) → connect (เบอร์จาก directory จริง,
  `tel:` เมื่อกดเท่านั้น); emergency profile เข้ารหัสฝั่ง server, ไม่ log/analytics
- a11y: roles/labels, focus trap, risk = icon+text+data attr, reduced motion, skip link; axe ใน component tests + Playwright
- 21 unit/component tests, ESLint (react/no-danger = error), tsc strict, production build (standalone) ผ่าน; Docker non-root

## 3. Acceptance checklist

- [x] 6 หน้าตรง reference ในระดับ layout/visual language และ interaction — `apps/web/docs/screen-inventory.md`
- [x] ทุกค่าปัจจุบันมาจาก API จริง มี freshness/source เปิดดูได้ — `DataFreshness`, `SourceList`, `DegradedBanner`
- [x] ไม่มี business rule สรุป final safety ใน browser — `ACTION_LABELS`/`RISK_LABELS` เป็น display map เท่านั้น (test)
- [x] map/markers/routes เป็น live interactive layers — `components/map/MapView.tsx` (GeoJSON sources, DOM markers)
- [x] old request ถูก cancel เมื่อ user แก้ — `LocationSearch` AbortController, TanStack `signal` ใน safety map
- [x] duplicate submit ถูกป้องกันและ mutation idempotent — `useStartAssessment`/`useApplyRoute` key per submission
- [x] loading/empty/error/degraded/partial/offline — skeletons, `EmptyState`, `ErrorState` (retry เฉพาะ retryable), `DataStatusBanner`
- [x] auth/token/location/medical info ปลอดภัย — cookie-only tokens, consent gates, sanitized markdown
- [x] responsive + WCAG 2.1 AA — component axe (badges) + E2E axe (serious/critical = 0) เมื่อรันกับ stack จริง
- [x] production Docker image non-root — `apps/web/Dockerfile`
- [ ] E2E/visual ต่อ stack จริง (login → recommendation → apply) — spec พร้อมใน `tests/e2e/journey.spec.ts`, ต้อง compose + Keycloak user (integration phase)
- [ ] Pixel comparison 1672×941 ทั้ง 6 หน้า — ทำได้หลัง stack จริงขึ้น (หน้า protected ต้องมี session)

## 4. Route / component tree

```text
app/layout.tsx (Providers: SessionProvider, QueryClient, Toast) — server
├─ (auth)/login/page.tsx — server (signIn server action)
└─ (app)/layout.tsx — server (auth() → AppShell userName)
   ├─ dashboard/page.tsx — client: SummaryCards, DetailSheet, DynamicMap, RecommendationCard, EmergencyQuickCard
   ├─ trips/new, trips/[tripId] — client: TripPlanner(TripForm+LocationSearch, DynamicMap, AssessmentProgress, RouteOptions)
   ├─ trips/[tripId]/compare — client: RouteColumn×2, DynamicMap, NotifyToggle
   ├─ safety-map — client: DynamicMap, layer switches (URL state), EventCard, timeline
   ├─ assistant/[conversationId] — client: MessageBubble(SafeMarkdown), quick chips, LiveLocationToggle
   └─ emergency — client: SosHoldButton + sosMachine, DynamicMap, EmergencyProfilePanel, nearby cards
api/auth/[...nextauth], api/backend/[...path] (proxy, SSE passthrough), api/health; proxy.ts (guard)
```

Server components only where no interactivity (layouts, login, redirects); all data pages are client components with
TanStack Query so freshness/refetch/abort semantics live in one place.

## 5. API hooks / query keys / cache policy

| Hook | Key | Stale | Notes |
| --- | --- | --- | --- |
| `useMe` | `["me"]` | 5 min | locale/timezone for formatting |
| `useTrips`/`useTrip` | `["trips"]`, `["trips", id]` | 30 s | ETag revision in data |
| `useRecommendation` | `["recommendations", id]` | 60 s | validated contract type |
| `useRun` | `["runs", id]` | polls 2 s while active (fallback when SSE disconnected) |
| `useMessages`/`useConversations` | `["conversations", id, "messages"]` | 30 s | invalidated after run completes |
| `useSafetyEvents` | `["safety", bbox, lookback, layers]` | 2 min | `keepPreviousData`, abort on key change |
| `useEmergencyContacts` | `["emergency","contacts",cc,sub]` | 10 min | directory version shown |
| mutations | — | — | no retry; `Idempotency-Key` on assessments/apply/resume |

Retry policy: only `ApiError.retryable` (429/5xx/network) up to 2×, honouring `Retry-After`; never on 4xx; never on auth.

## 6. Asset handling

`scripts/sync-assets.mjs` copies `assets/{branding,icons,illustrations,mascot}` → `public/assets` unchanged
(PNG alpha), attribution note written alongside; `next/image` serves AVIF/WebP at runtime. Fonts: system stack
(Segoe UI / Noto Sans Thai) — no download at build, no licence question.

## 7. Evidence

- `pnpm test` → 21 passed; `pnpm lint` → 0 errors (1 RHF compiler warning); `pnpm typecheck` clean;
  `next build` → 13 routes, proxy compiled; standalone server boots and `/api/health` = ok; `/login` renders;
  `/dashboard` without session → 307 `/login?next=/dashboard`; `/api/backend/me` without session → 401 JSON.
- Browser/device: Chromium (desktop pane) for smoke; Playwright projects `desktop-1672` + `mobile-390` for E2E.

## 13. Problems

| Problem | Resolution |
| --- | --- |
| Windows Application Control blocks unsigned native binaries (rolldown for Vitest 5/Vite 8) | pinned `vitest@3.2` + `vite@6` (esbuild binary is allowed); Next/Tailwind/lightningcss bindings load fine |
| `next start` incompatible with `output: standalone` | `scripts/start-standalone.mjs` copies static/public and runs `server.js` (same as Docker) |
| Next 16 proxy required a named `proxy` export | rewrote guard with `getToken` (edge-safe) instead of `auth()` wrapper |
| React Compiler lint: impure `Date.now` in render, setState in effects | `useNow()` ticker; SSE reducer resets via render-time state adjust; SOS uses ref callback |
| zod `.refine` narrowed form types away from `LocationRef` | null checks moved into `superRefine`; `confirmed_by_user` optional like the contract |

## 15. Limitations

| Limitation | Impact | Next |
| --- | --- | --- |
| Visual/pixel QA of protected pages not done in this environment | layout verified only via build + code review + login page render | run E2E visual baselines against compose stack |
| Alerts on the map are pinned at the destination (public `AlertItem` has no geometry) | approximate marker position for alerts; disaster events use real geometry | contract PR to add `geometry` to `AlertItem` |
| Marker clustering not enabled | thousands of markers would be slow | add Supercluster once real volumes are observed |
| Thai localisation is partial (labels EN, dates via `Intl` with user locale) | Thai users read English UI strings | i18n dictionary in a follow-up |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 2 | consumer of every public endpoint; expects `ETag`, `Retry-After`, SSE events per contract, 401 JSON | keep `public-api.yaml` in sync (web regenerates `lib/api/generated/public-api.d.ts`) |
| คน 8 | UI renders `short_summary`, `reasons`, `immediate_actions`, routes by label, `official_contacts`, `limitations` | keep labels honest; alternatives must carry `usable` |
| Team Lead | `apps/web/Dockerfile` (context = repo root), env keys documented, `NEXT_PUBLIC_*` build args | set `AUTH_URL`, `OIDC_*`, `API_INTERNAL_BASE_URL=http://api:8000` in `.env` |

ผู้จัดทำ: คน 1 (simulated) · วันที่: 2026-09-17
