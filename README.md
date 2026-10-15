# Mailroom

A single-owner, self-hosted mail app: a web mail client (read, triage, compose, over a local index
with live updates), a web UI for [imap-thingy](https://pypi.org/project/imap-thingy/) filters, the
host for the **mailroom-mcp** server, and — later — filter **suggestions** learned
from how the owner files mail. It composes standalone components rather than absorbing them:
[`docs/CONTEXT.md`](docs/CONTEXT.md).

**Status:** scaffold. The API answers `/api/v1/health` and the web app shows it; everything else is
in [`docs/TODO.md`](docs/TODO.md).

## Stack

| Part | What |
| --- | --- |
| `backend/` | Python 3.14+, FastAPI, SQLAlchemy 2 + psycopg 3, Alembic (schema `app`), uv |
| `web/` | React 19, Vite, TanStack Router, Tailwind 4 + shadcn, i18next, Zod, Vitest |
| Database | PostgreSQL 18 (`compose.yml` for dev) |
| Images | `mailroom-api`, `mailroom-web` (Podman; Quadlet sketch in `deploy/quadlet/`) |
| CI | Woodpecker (`.woodpecker/`), Release Please, Renovate |

Conventions, layout and tooling follow the owner's other projects; [`AGENTS.md`](AGENTS.md) is the working guide.

## Development

[mise](https://mise.jdx.dev) pins every tool and runs every task. From the repository root:

```bash
mise trust && mise install
mise run setup        # uv sync + pnpm install
mise run dev          # Postgres + API + Vite; Ctrl+C stops all three
```

Then open <http://127.0.0.1:5180>. Ports are fixed so another project and mailroom-mcp can run alongside:

| Service | Address |
| --- | --- |
| Web (Vite) | `127.0.0.1:5180` (proxies `/api`) |
| API | `127.0.0.1:8100` (`/docs` for OpenAPI) |
| Postgres | `127.0.0.1:5434`, user/password/db `mailroom`, tests in `mailroom_test` |
| Dev Dovecot | IMAPS `127.0.0.1:33993` (`mise run dev-mail`) |
| Dev Mailpit | SMTPS `127.0.0.1:33025`, UI <http://localhost:8026> |

Defaults live in `mise.toml`'s `[env]`; a root `.env` overrides them (see `.env.example`).

| Task | Does |
| --- | --- |
| `setup` | Install Python and Node dependencies |
| `dev` | `dev-db` + `dev-api` + `dev-web` together |
| `dev-db` / `dev-db-down` | Start (foreground) / stop the dev Postgres; data stays |
| `dev-api` / `dev-web` | API with reload (migrates at startup) / Vite |
| `dev-mail` / `dev-mail-down` / `dev-mail-reset` | Dev Dovecot + Mailpit with a local CA (`dev/.state/`) |
| `migrate` / `reset-db` | Alembic upgrade head / drop, recreate and migrate the dev database |
| `check-backend` / `check-web-types` / `check-web` | Ruff + mypy / tsc / ESLint + i18n + build |
| `check-migrations` | Upgrade → downgrade base → upgrade on a scratch database |
| `check-version-sync` / `check-scripts` | Release Please version copies / `node --test scripts/lib` |
| `check-all` | Every check (needs the dev database for `check-migrations`) |
| `test-backend` / `test-web` / `test-all` | pytest (needs `dev-db`) / Vitest / both |
| `fix` | Ruff and ESLint auto-fix, then mypy |
| `build-containers` | Build `mailroom-api` and `mailroom-web` (`-- staging` for staging tags) |
| `browser-check` | Report the browser chrome-devtools-mcp would drive |
| `clean` | Remove venv, caches, `node_modules`, `dist` |

## Documentation

[`docs/README.md`](docs/README.md) — what to read, in which order.
