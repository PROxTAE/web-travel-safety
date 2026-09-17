# web (คน 1)

Traveler-facing Next.js 16 app for the six screens in `assets/ui-screens`. It talks only to the public API of คน 2
through a same-origin proxy; it never calls providers or internal services and never computes a final risk/action.

```text
browser ──(HttpOnly Auth.js cookie)──► /api/backend/* (Route Handler) ──Bearer──► API /api/v1/*
                                       /api/auth/*   (Auth.js, Keycloak OIDC + PKCE, token refresh)
                                       proxy.ts      (route guard: no session → /login, API → 401 JSON)
```

| Route | Screen |
| --- | --- |
| `/dashboard` | 01 Dashboard overview |
| `/trips/new`, `/trips/[tripId]` | 02 Trip planner |
| `/safety-map` | 03 Global safety map |
| `/assistant/[conversationId]` | 04 AI assistant |
| `/emergency` | 05 Emergency center |
| `/trips/[tripId]/compare` | 06 Route comparison |

## Stack

Next.js 16 App Router · React 19 · TypeScript strict · HeroUI v3 + Tailwind CSS v4 (brand tokens in
`styles/globals.css`) · TanStack Query · React Hook Form + Zod · MapLibre GL (dynamic import) · Auth.js v5 ·
react-markdown + rehype-sanitize · Vitest + Testing Library + vitest-axe · Playwright + @axe-core/playwright.

## Commands

```bash
pnpm --dir apps/web gen:contracts   # copy generated contract types from packages/contracts
pnpm --dir apps/web gen:openapi     # openapi-typescript from packages/contracts/openapi/public-api.yaml
pnpm --dir apps/web sync:assets     # copy the approved asset pack into public/assets
pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web test
pnpm --dir apps/web build && pnpm --dir apps/web start   # standalone server (same entry as Docker)
pnpm --dir apps/web verify:no-mocks # fails if a runtime mock/worker/sample payload leaks into app code
E2E_BASE_URL=http://localhost:3000 E2E_USERNAME=… E2E_PASSWORD=… pnpm --dir apps/web test:e2e   # real stack only
```

## Environment

Server: `AUTH_SECRET`, `AUTH_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `API_INTERNAL_BASE_URL`.
Browser: `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_MAP_STYLE_URL` only (validated in `lib/env.ts`; any
`NEXT_PUBLIC_*SECRET|TOKEN|PASSWORD|KEY` aborts startup).

## Rules baked in

- Auth: tokens live only in the encrypted HttpOnly cookie; the proxy attaches them server-side; sign-out ends the Keycloak session.
- Data honesty: every card shows freshness (`DataFreshness`), sources (`SourceList`) and degraded/limitation banners; missing route labels render as "unavailable", never a fabricated card.
- Mutations: assessments/apply-route use a per-submission `Idempotency-Key`; PATCH/apply use `If-Match` and surface 412.
- SSE: `useRunEvents` uses EventSource (auto-reconnect + `Last-Event-ID`), a pure reducer dedupes by `event_id`, terminal events close the stream.
- Location: nothing is requested until the user asks (explainer → browser permission → consent record); stop sharing clears the watch immediately.
- Emergency: hold 3 s (pointer or keyboard) → confirm → optional consented share → numbers from the verified directory; `tel:` only on tap.
- Content: markdown is sanitized (`rehype-sanitize`), `dangerouslySetInnerHTML` is lint-banned, citations open with `noopener noreferrer`.
- Accessibility: roles/labels on every control, focus trap in sheets, risk shown as icon + text + data attribute (not colour only), reduced-motion respected, skip link.

## Tests

`pnpm test` → 21 passed (SSE reducer, API error mapping, trip timezone conversion + form schema, action/risk mappers,
SOS state machine, map helpers, route option honesty; components: badges + axe, degraded/error states, SOS hold
timing, assessment progress, route options). Playwright specs in `tests/e2e` run only against a real stack.
