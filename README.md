# Mailroom

A self-hosted mail app for a few separate users: a web mail client (read, triage, compose, over a local index
with live updates), a web UI for [imap-thingy](https://pypi.org/project/imap-thingy/) filters, its own
**MCP server**, and — later — filter **suggestions** learned
from how each user files mail. It composes standalone components rather than absorbing them:
[`docs/CONTEXT.md`](docs/CONTEXT.md).

**Status:** scaffold. The API answers `/api/v1/health` and the web app shows it; everything else is
in [`docs/TODO.md`](docs/TODO.md).

## Stack

| Part | What |
| --- | --- |
| `backend/` | Python 3.14+, FastAPI, SQLAlchemy 2 + psycopg 3, Alembic (schema `app`), procrastinate worker, uv; mailroom-core, imap-thingy |
| `web/` | React 19, Vite, TanStack Router, Tailwind 4 + shadcn, i18next, Zod, Vitest |
| Database | PostgreSQL 18 (`compose.yml` for dev) |
| Images | `mailroom-api`, `mailroom-web` (Podman; Quadlet sketch in `deploy/quadlet/`) |
| CI | Woodpecker (`.woodpecker/`), Release Please, Renovate |

[`AGENTS.md`](AGENTS.md) is the working guide: conventions, layout and tooling.

## Development

[mise](https://mise.jdx.dev) pins every tool and runs every task. From the repository root:

```bash
mise trust && mise install
mise run setup        # uv sync + pnpm install
mise run dev          # Postgres + API + worker + Vite; Ctrl+C stops them all
```

To sign in, create the first user once the database is up (`mise run migrate` if the API has not
run yet): `mise run create-user <name> --admin`. That admin adds the rest on the app's *Users*
page. For mail to try things on, `mise run dev-mail`, then `mise run seed-dev-mail <name>` gives
that user the dev Dovecot's accounts `dev` and `team` with sample mail. To try SSO, run `mise run
dev-idp` and put `MAILROOM_PUBLIC_URL=http://127.0.0.1:5180` and the `MAILROOM_OIDC_*` lines it
prints in `.env` (see `.env.example`); open the app at that same origin. With
`MAILROOM_PUBLIC_URL` set, the MCP server answers at `http://127.0.0.1:5180/mcp`
([`docs/deploy.md`](docs/deploy.md) — *MCP server and OAuth*).

Then open <http://127.0.0.1:5180>. Ports are fixed so other local dev stacks and mailroom-mcp can run alongside:

| Service | Address |
| --- | --- |
| Web (Vite) | `127.0.0.1:5180` (proxies `/api`, `/mcp`, `/oauth/`, `/.well-known/oauth-`) |
| API | `127.0.0.1:8100` (`/docs` for OpenAPI) |
| Postgres | `127.0.0.1:5434`, user/password/db `mailroom`, tests in `mailroom_test` |
| Dev Dovecot | IMAPS `127.0.0.1:33993` (`mise run dev-mail`) |
| Dev Mailpit | SMTPS `127.0.0.1:33025`, UI <http://localhost:8026> |
| Dev OIDC provider | `http://127.0.0.1:8190` (`mise run dev-idp`) |

Defaults live in `mise.toml`'s `[env]`. To change one, set it in a gitignored `mise.local.toml` under `[env]`; a root `.env` only adds variables `mise.toml` does not set (see `.env.example`).

| Task | Does |
| --- | --- |
| `setup` | Install Python and Node dependencies |
| `deps-local` / `deps-pinned` | Swap mailroom-core and imap-thingy for local editable checkouts / back to the pinned ones |
| `dev` | `dev-db` + `dev-api` + `dev-worker` + `dev-web` together |
| `dev-db` / `dev-db-down` | Start (foreground) / stop the dev Postgres; data stays |
| `dev-api` / `dev-web` | API with reload (migrates at startup) / Vite |
| `dev-worker` | procrastinate worker (waits for the API's migrations; restart it after a change) |
| `dev-mail` / `dev-mail-down` / `dev-mail-reset` | Dev Dovecot + Mailpit with a local CA (`dev/.state/`) |
| `migrate` / `reset-db` | Schema `core`, then Alembic upgrade head / drop, recreate and migrate the dev database |
| `create-user` | Create a user in `DATABASE_URL` (`<name> [--admin]`; the password is prompted for) |
| `dev-idp` | A fake OpenID Connect provider for trying SSO: approves everyone at once (`--username`, `--subject`; `/mint` for an access token) |
| `seed-dev-mail` | Give a user the dev Dovecot accounts `dev` and `team`, sample mail in each empty INBOX (`<name>`) |
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

## License

[GPL v3](https://www.gnu.org/licenses/gpl-3.0.en.html) or later, like the rest of the Mailroom family. See [`LICENSE`](LICENSE).
