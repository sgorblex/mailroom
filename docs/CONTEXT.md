# Product context

## What Mailroom is

One person's mail, in one place they run themselves: a **web mail client** over their IMAP/SMTP
accounts, a **filters UI** for the rules that sort that mail on the server, the **host for the MCP
server** that lets Claude work with it, and — later — **suggestions** for new filters learned from
how the owner files mail by hand.

## Audience

**One owner.** One login; every account belongs to that owner. There are no other users, no
sharing, no tenants.

## The component family

Mailroom the app is the top of a small family. Every member is a **standalone project** with its own
repository, release line and deployment, and **dependencies only point downwards**:

```
Mailroom app (this repo)
  ├─ mailroom-mcp   MCP server: OAuth 2.1 AS, profiles, /mcp/<profile>      (repo mailroom-mcp)
  │    └─ mailroom-core   accounts, sealed secrets, IMAP/SMTP, send gate,   (same repo, own package)
  │                       rendering, audit
  └─ imap-thingy     IMAP filter library and runner (PyPI)

filterd      the owner's personal filter daemon (private), also on imap-thingy;
                  not a dependency, and expected to be replaced by the app
```

- **mailroom-core / mailroom-mcp** stay usable on their own: standalone, mailroom-mcp supplies its
  own account store, owner login, audit sink and settings page. Inside the app, the **app supplies
  each of those** (`AccountStore`, `OwnerAuth`, `AuditSink`, `settings_ui` off) and mounts the MCP's
  ASGI app at `/mcp/<profile>` in its own API process. The MCP keeps its own schema (`mcp`) and
  Alembic history; the app's tables live in schema `app`.
- **filterd** is the owner's personal rules and daemon, not part of the family's API: the
  reusable runner (run, IDLE monitor, periodic, starred-sort) moves into imap-thingy, and the app
  depends on imap-thingy alone. filterd keeps running until the app's worker takes a mailbox
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

- Multiple users, sharing, delegation, or multi-tenancy.
- A mail server: Mailroom is a client of existing IMAP/SMTP servers. No Sieve management (server-side
  rules stay with imap-thingy).
- POP3, Exchange/EWS, Microsoft Graph.
- **Autonomous action.** Nothing Mailroom learns ever moves, flags, deletes or sends mail by itself;
  it suggests, and the owner decides.
- Marketing, tracking or bulk-mail features.

## Security posture

Mail is **hostile input**. HTML mail renders only in a **sandboxed iframe** under a **strict CSP**,
with **no remote content by default**; the app itself is single-owner and private. Detail:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
*Security*.
