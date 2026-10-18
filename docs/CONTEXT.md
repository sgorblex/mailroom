# Product context

## What Mailroom is

Mail for a few people, each kept separate, on one instance they run themselves: a **web mail
client** over each user's IMAP/SMTP accounts, a **filters UI** for the rules that sort that mail on the server, the **host for the MCP
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
Mailroom app (this repo)
  ├─ mailroom-mcp   MCP server: OAuth 2.1 AS, profiles, /mcp/<profile>      (repo mailroom-mcp)
  │    └─ mailroom-core   accounts, sealed secrets, IMAP/SMTP, send gate,   (same repo, own package)
  │                       rendering, audit
  └─ imap-thingy     IMAP filter library and runner (PyPI)

filterd      a personal filter daemon (private), also on imap-thingy;
                  not a dependency, and expected to be replaced by the app
```

- **mailroom-core / mailroom-mcp** stay usable on their own: standalone, mailroom-mcp supplies its
  own account store, owner login, audit sink and settings page. Inside the app, the **app supplies**
  `OwnerAuth` and `AuditSink` and turns `settings_ui` off, keeps the accounts in mailroom-core's
  schema `core` in its own database (`AccountStore`), and mounts the MCP's ASGI app at
  `/mcp/<profile>` in its own API process. mailroom-mcp learns to serve several users in its 0.4,
  so each app user gets their own MCP ([`TODO.md`](TODO.md)). Each package keeps its own schema
  (`core`, `mcp`) and Alembic history; the app's tables live in schema `app`.
- **filterd** is one person's own rules and daemon, not part of the family's API: the
  reusable runner (run, IDLE monitor, starred-sort) lives in imap-thingy as `imap_thingy.runner`,
  and the app depends on imap-thingy alone. filterd keeps running until the app's worker takes a mailbox
  over, and only after it stops acting on that mailbox. There is never a double runner.
- **imap-thingy** stays a general-purpose library on PyPI; the app reads and writes its rules
  through the library's own serialisation, never a private format.
- **No component imports the app**, and none learns about it. A need that seems to require that is a
  design question, answered by an integration point in the lower component.

Today (scaffold) the app depends on none of them yet; each dependency arrives with the first
feature that needs it ([`TODO.md`](TODO.md)).

## Client scope

Between **tier 2** and **tier 3** of [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Mail client*: reading
and triage plus composing, over a **local index** with **live updates** (IMAP IDLE), first. Tier 3
features come later, one by one, when they earn their place.

## Reversed non-goals

mailroom-mcp's design ruled out **a webmail UI** and **filter management**. Mailroom is where both
now live — deliberately outside the MCP server, which stays a small, separately deployable
component.

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
