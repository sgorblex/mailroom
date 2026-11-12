# Product context

## What Mailroom is

Mail for a few people, each kept separate, on one instance they run themselves: a **web mail
client** over each user's IMAP/SMTP accounts, a **filters UI** for the rules that sort that mail on the server, its **own MCP
server** that lets Claude work with it, and — later — **suggestions** for new filters learned from
how each user files mail by hand. The filters UI comes first; the client follows.

## Audience

**A few users on one instance.** Each has their own login, and their own mail accounts, filters
and suggestions, which nobody else sees. An admin creates the users; there is no public sign-up.
Nothing is shared between users.

## The component family

Mailroom the app is the top of a small family, and **dependencies only point downwards**:

```
Mailroom app (this repo)  web client, filters UI, own MCP server at /mcp (OAuth, SSO)
  └─ mailroom-core        accounts, sealed secrets, IMAP/SMTP, send gate,  (backend/core,
                          rendering, audit, the filter engine               workspace package)

mailroom-mcp              standalone MCP server for mail (repo mailroom-mcp, retired, archived)
```

- **The app has its own MCP server** (`mailroom-app`, [`requirements/mcp.md`](requirements/mcp.md)),
  written in the app, over the app's own services, for each user's own data. It starts with
  filters (rule sets, dry runs, runs), which mailroom-mcp lacks, plus the accounts and folders
  listing they need. It writes its own mail tools over mailroom-core, turning them on group by
  group ([`requirements/mcp.md`](requirements/mcp.md)), `mail_read` first, `mail_draft` and
  `mail_send` with the compose slice.
- **mailroom-mcp is retired** (decided on 2026-11-09, done on 2026-11-10): the app's MCP covers what
  it was used for, the audit trail included; its deployment is gone and its repository is archived.
  The app never embedded or imported it. mailroom-core started there, as the Python port's shared
  layer, and accounts in schema `core` are byte-compatible, so mailroom-mcp's data imports
  unchanged.
- **The app's built-in OAuth authorization server is self-contained** (the database and a small
  user interface, no app routes), so it may move to a shared package later.
- **mailroom-core** ([`core.md`](core.md)) is the mail layer: `render`, `mail` (connections in
  `mail.imap`, read, organize, folders, compose, drafts) and `send` (SMTP and the send gate), which the mail
  client and the MCP mail tools are built on. It lived in mailroom-mcp until 0.1.3, its last PyPI
  release, and is now a workspace package in `backend/core`, with its history, released only as
  part of the app. It never imports `mailroom`. The app keeps the accounts (`AccountStore`) in
  mailroom-core's schema `core` in the app's database, under mailroom-core's own Alembic history
  (`core.alembic_version`); the app's tables live in schema `app`.
- **The filter engine**, `mailroom_core.filters` ([`core.md`](core.md) — *Filters*), was
  imap-thingy, a library on PyPI; it is part of mailroom-core since 2026-11-11,
  with its history. The app reads and writes its rules through the engine's own serialisation,
  never a private format, and runs them with its runner (run, IDLE monitor, starred-only).
- **No component imports the app**, and none learns about it. A need that seems to require that is a
  design question, answered by an integration point in the lower component.

The backend depends on **mailroom-core** as a uv workspace member (`backend/pyproject.toml` is
the workspace root). mailroom-core's pytest plugin (no-network guard, mail and store fixtures) loads in
the backend's tests; core's own tests run separately, from `backend/core` (`mise run test-core`).

### Where the family is heading

Decided on 2026-11-09, in this order ([`TODO.md`](TODO.md) — *Family roadmap*):

- **mailroom-core folds into this repository** as a workspace package, since the app is now its
  only consumer. That ends the step of releasing core and then bumping the app's pin. Done:
  `backend/core`.
- **Editions:** an instance chooses its features in config (`MAILROOM_FEATURES`,
  [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Editions*). There is always a base: users, sign-in,
  accounts and their secrets, the settings screens (profile, accounts, users), and Postgres. On
  top of it, the mail client, the MCP server (with its `/connect` consent screen and connected
  apps) and filters are each optional, and the MCP mail tools do not need the mail client. A
  disabled feature runs nothing: no routes, worker or screens. Every edition uses Postgres. Done:
  the API, the worker and the web app.
- **This repository is public** since 2026-11-10, after an audit of its whole history, rewritten
  where it named private things.

Decided on 2026-11-11: **one library under one app.**

```
Mailroom app (mailroom)   users, sign-in, OAuth, editions, storage of accounts and rule sets,
  │                       audit, REST API, MCP server, worker, web
  ▼
mailroom-core             render, mail (IMAP, IDLE included), send, filters, crypto, store
(mailroom_core)
```

- **imap-thingy folds into mailroom-core** as `mailroom_core.filters`, with its history, and is
  no longer published: its last PyPI release says where it went (folded in on 2026-11-11).
  Nearly every change to it came from the app, each costing a release and a pin bump;
  unpublished, a package of its own would only add a second build, config and test setup; and
  running on core's IMAP layer, nothing sets it apart. Core and the app already pair this way: `mailroom_core.mail` is the library,
  `mailroom.mail` the feature, and so for `filters`.
- **One IMAP stack** (done 2026-11-12). The engine runs on core's connections: its criteria are
  core's searches and its actions core's moves and flags, so a filter's move is the same
  operation as one in the client. IDLE lives in `mailroom_core.mail`, for the worker's watches and
  the client's live updates alike, and the worker and the API reach mail through the same
  `MailPool`. It came before folder management, so that it, the move across accounts and the
  filter action that moves to another account are each written once, in core.
- **Core has a public API:** what each of its subpackages exports in `__all__`, with a docstring
  on every public name. The app imports those, never a submodule, and inside core `filters` may
  use `mail`, never the other way round. There is no semver while core is unpublished: a breaking
  change fixes its callers in the same pull request and is marked `!` with scope `core`, so the
  app's changelog shows it.
- **Core filters on its own:** accounts from plain config, rules in Python or as specs, a run, a
  dry run or an IDLE monitor, and their reports, with no app, database or server. A test keeps it
  so: `core/tests/filters/test_engine_mail.py`, built from `connections_from` and a settings
  dict. A script installs core from this repository, pinned to a Mailroom tag.
- **Core is published again** only when someone besides the owner wants to use it, or when the
  `run` command (below) is meant for others to install: then as `mailroom-core` on PyPI, in
  semver from 0.x, with `store` and its database packages an optional extra.
- **Open: core without the database.** Moving the store, the send gate's claims and the config
  into the app, with core taking its accounts through an interface, pays off only if a core free
  of the database matters. Decided when the `run` command is designed.
- **Command-line tools:**
  - a client for a running instance, after personal API tokens exist;
  - `run`, for filters with no server at all, over mailroom-core.

## Client scope

Between **tier 2** and **tier 3** of [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Mail client*: reading
and triage plus composing (plain text, with the send mode limiting what agents send), over a **local index** with **live updates** (IMAP IDLE), first. Tier 3
features come later, one by one, when they earn their place.

## Reversed non-goals

mailroom-mcp's design ruled out **a webmail UI** and **filter management**. Mailroom is where both
now live — deliberately outside mailroom-mcp, now retired (*The component family*). Filter management reaches models through the app's own MCP server instead.

## Non-goals (unless requirements say otherwise)

- Sharing between users, delegation, or multi-tenancy across instances. Users are separate.
- A mail server: Mailroom is a client of existing IMAP/SMTP servers. No Sieve management (server-side
  rules stay with the filter engine).
- POP3, Exchange/EWS, Microsoft Graph.
- **Autonomous action.** Nothing Mailroom learns ever moves, flags, deletes or sends mail by itself;
  it suggests, and the user decides.
- Marketing, tracking or bulk-mail features.

## Security posture

Mail is **hostile input**. HTML mail renders only in a **sandboxed iframe** under a **strict CSP**,
with **no remote content by default**; the app itself is private, and each user sees only their own mail. Detail:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
*Security*.
