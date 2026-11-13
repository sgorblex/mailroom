# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Across the family

Mailroom is built on projects with their own repositories and checklists. As of 2026-11-12:

- **mailroom-mcp** (`../mailroom-mcp`, GitHub `sgorblex/mailroom-mcp`): retired on 2026-11-10 and
  archived; its Python port's core lives in `backend/core`.
- **imap-thingy** (`../imap-thingy`, GitHub `sgorblex/imap-thingy`): folded into mailroom-core
  as `mailroom_core.filters` on 2026-11-11; its last PyPI release, 0.8.1, points here, and its
  repository is archived.

## Family roadmap

In order. The reasons are in [`CONTEXT.md`](CONTEXT.md) — *Where the family is heading*.

- [x] **Fold mailroom-core into this repository** as a workspace package, with its history.
- [x] **Editions:** features chosen in config (mail client, MCP, filters) over an always-present
      base.
- [x] **Retire mailroom-mcp** (2026-11-10): the app's MCP covers its use, the audit trail
      included; its deployment is gone and its repository archived.
- [x] **Go public:** the whole history audited and rewritten where it named private things, and
      the repository public since 2026-11-10 (the earlier one stays private as
      `mailroom-archive`, with its pull requests).
- [ ] **One library under one app**, in this order:
  - [x] **Fold imap-thingy into mailroom-core** (2026-11-11): as `mailroom_core.filters`, with its
        history, its tests among core's and the app's imports moved over; the helpers only
        scripts used gone. Its last PyPI release, 0.8.1, says where it went; its repository is
        archived, and its Woodpecker repository and PyPI trusted publisher are gone.
  - [x] **Core's public API** (2026-11-11): ruff's `D1` rules on core's source, with the missing
        docstrings written; tests that the app imports only the subpackages' facades, that
        `mail` never imports `filters` and that core never imports the app.
  - [x] **One IMAP stack** (2026-11-12): the engine on core's connections, its searches, moves
        and flags core's, the client's move checks included; IDLE in `mailroom_core.mail`; the
        worker's watches and runs and the API's dry run on the app's mail pools, and
        `mailroom.accounts.mail` gone, so STARTTLS accounts filter too. Connections from plain
        config (`connections_from`), and `core/tests/filters/test_engine_mail.py` running
        filters with no app or database.
  - [x] **Engine parsing on core's MIME** (2026-11-13): the engine's criteria read the messages
        they fetch through `mailroom_core.mail.parsed_message` instead of mail-parser, which left
        the dependencies; an HTML-only body is matched as its visible text.
  - [ ] **Tidy-ups**, whenever convenient: mailroom-mcp's leftovers out of core
        (`STANDALONE_USER`, docs that name it); `mailroom/mail/service.py` and
        `mailroom/api/routes/mail.py` split by concern, as core's `mail` is.
- [ ] **Command-line tools:**
  - [ ] a client for a running instance, after personal API tokens;
  - [ ] `run`, for filters with no server, over mailroom-core; it settles whether core keeps the
        database ([`CONTEXT.md`](CONTEXT.md) — *Where the family is heading*).

## Client

- [ ] **Tier 1** (slice A, [`requirements/mail-client.md`](requirements/mail-client.md)):
  - [x] **Core mail read** — `render` and `mail` (with `mail.imap`) in mailroom-core 0.1.1, the
        app's pin.
  - [x] **REST** — folders, message list and search, message view, HTML document, part download.
  - [x] **Web** — `/mail`: folders with counts, list with load more, message view with the
        sandboxed HTML iframe and remote-content gate.
  - [x] **MCP** — the `mail_read` group.
- [ ] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [x] **Tier 2 triage** (slice B, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      move, archive, trash and flag over REST, the web triage (multi-select, toolbar, shortcuts,
      mark read on open) and the `mail_organize` MCP group.
- [x] **Tier 2 compose** (slice C, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      compose, reply, reply all, forward, drafts and attachments through mailroom-core's send
      gate: compose, drafts and send in mailroom-core 0.1.3, the REST routes,
      the web composer and the `mail_draft` and `mail_send` MCP groups.
- [x] **Host proxy limit for drafts** — the production proxy sets no body limit, and its read
      timeout is now 300 s, so a 30 MB draft has time to arrive.

## Folders and moves across accounts

In this order, after *One IMAP stack* (*Family roadmap*): each builds on the one before.

- [x] **Folder management** — create, rename (moving it in the hierarchy) and delete an account's
      folders, from the web and MCP. A rename updates the rule sets that name the folder. The
      first change that deletes permanently also brings the safeguards every later one keeps:
      the docs' "never deletes permanently" becomes "deletes only deliberately" (the rule was
      lifted on 2026-11-10), a confirmation on the web, a destructive-marked MCP tool, never a
      side effect, refusal tests and audit records.
- [ ] **Move messages across accounts** — written once in `mailroom_core.mail`, so filters can
      use it too: copy the raw message with its flags and date, confirm it arrived by
      `Message-ID`, then remove it from the source (to Trash, or for good). A retry first checks
      the target, so it never copies twice; an append whose removal failed is reported, the
      message then in both. Gmail's All Mail and size limits apply. Mailroom offers it as
      *Move to account…* on the web and an MCP tool, both recorded on each side.
- [ ] **Filters that move to another account** — a `mailroom_core.filters` action naming the
      target account, resolved by the host at load time (the user's own only, checked on save
      and at run), with an account and folder picker in the filter editor. Needs a guard against loops (A to B and
      B to A, or the target's new-mail filters picking the message up again) and a run that
      writes to two mailboxes.

## MCP and suggestions

- [ ] **MCP server** ([`requirements/mcp.md`](requirements/mcp.md)), in this order:
  - [x] **Filters service layer** — rule-set and filter logic out of the routes, so the API and the
        MCP share it.
  - [x] **Web SSO** — OIDC sign-in for existing users, linked sign-ins on the profile.
  - [x] **MCP endpoint and tools** — `/mcp`, the `filters_*` groups, the envelope, the audit line.
  - [x] **Built-in OAuth server** — DCR, CIMD, tokens, the `/connect` consent screen.
  - [x] **External issuer** — JWTs from Authentik or Keycloak, mapped through linked sign-ins.
  - [x] **Connected apps** — list and revoke grants on the profile.
  - [x] **Mail tool groups** — each with its client slice: `mail_read`, `mail_organize`,
        `mail_draft`, `mail_send` and `mail_folders` are on.
  - [ ] **Consent per tool group** — record the tool groups each grant was approved for, and ask
        for fresh consent when a grant's client calls a group added after it (a grant approved
        before `mail_read`, `mail_organize`, `mail_draft`, `mail_send` or `mail_folders` reads,
        moves, manages folders and, within the send mode, sends mail without asking again); before
        other users join.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.

## Engineering and operations

- [x] **First deploy** — production since 0.2.0: database and role `mailroom` on the shared
      Postgres, backup line, Quadlet units, Traefik route.
- [x] **Release pipeline** — a release tag builds and pushes both images; the host's config
      repository deploys them.
- [ ] **Staging pipeline** — a staging instance, deployed from CI.
- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
