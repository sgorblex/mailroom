# Mailroom web

React 19 SPA: Vite, TanStack Router, Tailwind 4 + shadcn (`components.json`), i18next (`en`, `it`),
Zod-validated API calls (`src/api/http.ts`). ESLint `strictTypeChecked`, no Prettier — as another project.

- **Dev:** `mise run dev-web` (or `mise run dev` for db + API + web) serves on
  `http://127.0.0.1:5180` and proxies `/api` to the API on `127.0.0.1:8100`; both ports come from
  mise's `[env]` (`MAILROOM_VITE_PORT`, `MAILROOM_API_PORT`), overridable in the root `.env`.
- **Checks:** `mise run check-web-types` (fast), `mise run check-web` (lint, i18n parity, build),
  `mise run test-web`.
- **Tests:** a file named `*.node.test.ts(x)` runs in bare Node; every other test runs in jsdom
  with `src/test/setup.ts`.
- **Locales:** `src/locales/<lng>/<namespace>.json`; English is the source, `i18n:check` keeps
  Italian in parity.
- **Image:** `web/Containerfile` builds the SPA and serves it from nginx on 8080, proxying `/api/`
  to the API in the same pod (`nginx-default.conf`, which also sets the CSP).
