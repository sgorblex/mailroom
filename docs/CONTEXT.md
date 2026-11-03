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

Mailroom the app is the top of a small family. Every member is a **standalone project** with its own
repository, release line and deployment, and **dependencies only point downwards**:

```
Mailroom app (this repo)  web client, filters UI, own MCP server at /mcp (OAuth, SSO)
  ├─ mailroom-core        accounts, sealed secrets, IMAP/SMTP, send gate,  (repo mailroom-mcp,
  │                       rendering, audit                                  own package)
  └─ imap-thingy          IMAP filter library and runner                   (PyPI)

mailroom-mcp              standalone MCP server for mail, on mailroom-core (repo mailroom-mcp)
```

- **The app has its own MCP server** (`mailroom-app`, [`requirements/mcp.md`](requirements/mcp.md)),
  written in the app, over the app's own services, for each user's own data. It starts with
  filters (rule sets, dry runs, runs), which mailroom-mcp lacks, plus the accounts and folders
  listing they need. It writes its own mail tools over mailroom-core, turning them on group by
  group ([`requirements/mcp.md`](requirements/mcp.md)), `mail_read` first.
- **mailroom-mcp stays a maintained standalone server**, for people who do not run the app, and
  keeps serving mail reading and sending meanwhile. The app does not embed or import it; the two
  share mailroom-core, and accounts in schema `core` are byte-compatible, so mailroom-mcp's data
  imports unchanged.
- **The app's built-in OAuth authorization server is self-contained** (the database and a small
  user interface, no app routes), so it may move to a shared package later.
- **mailroom-core** provides `render` and `mail` (connections in `mail.imap`) from 0.1.1, which the
  mail client is built on. It stays usable on its own. The app keeps the accounts (`AccountStore`) in
  mailroom-core's schema `core` in the app's database, under mailroom-core's own Alembic history
  (`core.alembic_version`); the app's tables live in schema `app`.
- **imap-thingy** stays a general-purpose library on PyPI; the app reads and writes its rules
  through the library's own serialisation, never a private format, and runs them with its runner
  (`imap_thingy.runner`: run, IDLE monitor, starred-only).
- **No component imports the app**, and none learns about it. A need that seems to require that is a
  design question, answered by an integration point in the lower component.

The backend depends on **mailroom-core** and **imap-thingy**, both from PyPI. For a change that spans repositories,
`mise run deps-local` installs local editable checkouts in their place (the siblings of the main
checkout, or `MAILROOM_CORE_PATH` / `IMAP_THINGY_PATH`) without touching a tracked file, and
`mise run deps-pinned` switches back. mailroom-core's pytest plugin (no-network guard, store
fixtures) loads in the backend's tests.

## Client scope

Between **tier 2** and **tier 3** of [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Mail client*: reading
and triage plus composing, over a **local index** with **live updates** (IMAP IDLE), first. Tier 3
features come later, one by one, when they earn their place.

## Reversed non-goals

mailroom-mcp's design ruled out **a webmail UI** and **filter management**. Mailroom is where both
now live — deliberately outside mailroom-mcp, which stays a small, separately deployable
component. Filter management reaches models through the app's own MCP server instead.

## Non-goals (unless requirements say otherwise)

- Sharing between users, delegation, or multi-tenancy across instances. Users are separate.
- A mail server: Mailroom is a client of existing IMAP/SMTP servers. No Sieve management (server-side
  rules stay with imap-thingy).
- POP3, Exchange/EWS, Microsoft Graph.
- **Autonomous action.** Nothing Mailroom learns ever moves, flags, deletes or sends mail by itself;
  it suggests, and the user decides.
- Marketing, tracking or bulk-mail features.

## Security posture

Mail is **hostile input**. HTML mail renders only in a **sandboxed iframe** under a **strict CSP**,
with **no remote content by default**; the app itself is private, and each user sees only their own mail. Detail:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
*Security*.
