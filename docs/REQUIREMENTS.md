# Requirements

**Documentation index:** [README.md](README.md). Product context and the component family:
[CONTEXT.md](CONTEXT.md).

Each capability carries a **maturity**:

| Maturity | Meaning |
| --- | --- |
| **Planned** | Agreed direction; not yet specified enough to build against. |
| **Current** | In scope now: defined enough to build against; acceptance evolves with development. |
| **Partial** | Some behaviour exists; gaps are listed inline. |
| **Done** | Meets the current slice (still revisable when priorities shift). |

---

## Platform skeleton

**Maturity: Done** — FastAPI API with `GET /api/v1/health` (liveness for the SPA), `/healthz`
(container healthcheck), `/api/v1/ready` (database reachable) and `/api/v1/version`; Alembic in
schema `app` with its own version table; the web app's *API status* page (`/health`, in the user
menu) shows the health check through the Vite proxy. Engineering baseline:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md).

## Users and accounts

**Maturity: Done** — sign-in, managing users and each user's mail accounts.

- **Users** sign in with a username and password (passkeys later). Every route except health,
  readiness, version, sign-in, sign-out and the signed-in check requires a
  [user session](GLOSSARY.md); the web app shows the sign-in form in place of any page until then.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Sign-in*.
- **Users are separate.** Each user's accounts, filters and suggestions are theirs alone; nothing
  is shared. An [admin](GLOSSARY.md) adds and removes users and resets their passwords on the
  *Users* page (the first admin comes from `mise run create-user <name> --admin`), and changes the
  [instance settings](GLOSSARY.md) in its *Instance* section; there is no public
  sign-up. Every user changes their own password on their *Profile* page. Either can revoke the
  user's API tokens and connected apps too.
- **Sessions.** Each user sees on their *Profile* page the browsers they are signed in on (browser
  and system, when last seen and signed in, the IP address as far as the instance's setting goes,
  and which one is this device), and signs out one or every other one.
- **Time zone.** Each user keeps a time zone (an IANA name), picked on their *Profile* page and
  filled from the browser's zone when they first sign in to the web; over MCP, UTC until set. The
  web's dates (a list's "today" included), the MCP read tools' dates (each answer names the zone),
  reply and forward quotes, the send limit's refusal and the filters' `older_than_days` use it. How:
  [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
  *Sign-in*.
- **Mark all as read.** Each user picks on their *Profile* page what "Mark all as read" on a
  folder with subfolders does first: the folder and its subfolders (the default) or the folder
  alone; the folder's menu offers the other second
  ([`requirements/mail-client.md`](requirements/mail-client.md) — *Folder management*).
- **Remote images.** Each user lists on their *Profile* page the senders whose remote images
  they always show, and removes them there; a sender is added only from a message whose DMARC
  passed ([`requirements/mail-client.md`](requirements/mail-client.md) — *Remote content*).
- **Read as text.** A message with HTML opens in it; choosing Text on one remembers its sender,
  whose mail then opens as text, and choosing HTML forgets it. Each user lists those senders on
  their *Profile* page and removes them there
  ([`requirements/mail-client.md`](requirements/mail-client.md) — *Sender views*).
- **Appearance.** Each user picks on their *Profile* page the web's theme (light, dark, or as the
  device), its accent colour (teal by default, blue, violet, green, terracotta or rose), its text
  size (small, default, large), the page the web opens on (Home, or Mail, Filters, Accounts or
  Activity), the density of the message lists (comfortable, or compact rows
  without each message's opening) and where an open message shows on a wide screen (right of the
  list, below it, or off: in the list's place), and what swiping a message row left or right does on
  a touch screen (trash and archive by default; mark read, star, To sort or nothing —
  [`requirements/mail-client.md`](requirements/mail-client.md)); a choice shows at once and is the same on every device they sign in
  on. How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Own appearance*.
- **Accessibility.** The web is used by keyboard alone, shows where focus is, keeps AA contrast in
  every theme and accent, and moves less for whoever asks their device for less motion; automated
  checks run on every main screen. How:
  [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
  *Accessibility*.
- **Navigation.** Every signed-in page sits under one header: the Mailroom mark, the instance's
  pages with an icon each (Mail, Filters and Activity only where it runs them, Users only to an
  admin), the current one marked, and a user menu with the Profile, the API status and Sign out. On a phone the
  header keeps the mark and the user menu, and a tab bar at the bottom holds Home, Mail and Filters
  (as the instance has them), with the other pages, the Profile and Sign out under More. Both menus
  work from the keyboard (the ARIA menu button pattern). The web app installs on a phone's home
  screen or as a desktop app, with Mailroom's icon (on staging, as *Mailroom staging* with the icon
  banded in amber); it still needs the server, nothing works offline.
- **Profile.** The *Profile* page groups its settings as Account (password, time zone, linked
  sign-ins, sessions, *Your data*), Appearance, Mail, Sending (Undo send, signatures, vacation reply, limits) and Apps
  (connected apps, API tokens), each group only where the instance has its settings, with a nav to them that
  marks the one in view; a link names a group as `/profile#<group>`.
- **Home.** The web opens on a summary of the user's mail and filters: unread mail per account
  inbox and in all, what waits on them (starred, To sort, snoozed coming back today, scheduled
  sends), each rule set's recent runs and failures and its week's messages acted on, and a tile
  per page, each part only where the instance runs its feature. The Profile's *Open on* makes the
  app open on Mail, Filters, Accounts or Activity instead, Home in the nav still showing the
  summary ([`requirements/mail-client.md`](requirements/mail-client.md#home)).
- **Undo send.** Each user picks on their *Profile* page how long the web's Send holds a
  message for an Undo, 0 (off) to 30 seconds, 10 by default
  ([`requirements/mail-client.md`](requirements/mail-client.md#undo-send)).
- **Send later.** The composer schedules a message for a time in the user's time zone, and the
  mail screen's *Scheduled* list cancels or edits what is scheduled; an agent schedules with
  `draft_send`'s `send_at` ([`requirements/mail-client.md`](requirements/mail-client.md#send-later)).
- **Snooze.** A message's *Snooze* moves it to the account's Snoozed folder until a time in the
  user's time zone, when the worker brings it back unread; the mail screen's *Snoozed* list
  brings one back sooner, and an agent snoozes with `messages_snooze`
  ([`requirements/mail-client.md`](requirements/mail-client.md#snooze)).
- **Vacation reply.** Each account's automatic answer while the user is away, set on their
  *Profile* page (on and off, first and last days, subject, rich text, identity): the worker
  answers a person's new mail addressed to the account, never a list, bulk or automatic mail, a
  no-reply or own sender, each sender once in 4 days, through the send gate as the user
  ([`requirements/mail-client.md`](requirements/mail-client.md#vacation-reply)).
- **Signatures.** Each user writes on their *Profile* page a signature for each identity of
  their accounts, which their messages from it start with
  ([`requirements/mail-client.md`](requirements/mail-client.md#signatures)).
- **Index.** Each user picks on their *Profile* page how far back the mail index keeps their
  mail and, per account, which folders it keeps, ticked in a tree with a filter and all/none,
  each with its sync state and counts; while a folder the mail screen lists or searches is first
  being indexed, a line above the list says how far it has come ([`requirements/mail-client.md`](requirements/mail-client.md) — *Index*).
- **Single sign-on** (optional): "Sign in with …" an OpenID Connect provider, for users an admin
  already created; nobody signs up through it. A user links the provider's sign-in on their
  *Profile* page (and unlinks it there), or, when the instance says so, their first SSO sign-in
  links by username. How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Single sign-on*.
- **Accounts** (IMAP + SMTP, identities, send mode, an optional name, unique among the user's accounts, and colour) come from **mailroom-core**'s model; the app
  keeps them in mailroom-core's own schema (`core`, its `AccountStore`) in the app's database, with secrets sealed as
  mailroom-core seals them (byte-compatible with mailroom-mcp, so its data imports unchanged).
- Each user manages their own accounts on the *Accounts* page: add (the instance numbers the
  account: no id to choose), edit (an empty password keeps the stored one; every [identity](GLOSSARY.md), with which one is the default;
  the account's name and colour; its **authentication server**, whose sender checks alone are
  believed: [sender authentication](GLOSSARY.md)),
  test the IMAP login, remove (after a confirmation; the mail on the server stays), and, where
  the instance runs filters, a per-account **Run filters** switch, off until turned on. mailroom-core's config models check every change, so a refusal names the
  field. Removing a user removes their accounts, stored passwords and send log with them, and the
  senders whose remote content they show.
  The web names an account by its display name (its name, else its login) with a dot of its
  colour, never by its id; the API and MCP name it by that id, a number. An account that is gone
  (named by a rule set or an audit record) shows as *Removed account*. A name another of the
  user's accounts has is refused on the name field.
  The API suggests an address's IMAP and SMTP servers (the domain's own autoconfig, DNS SRV, its
  MX against known providers, Thunderbird's ISPDB, then presets; Microsoft marked unsupported until
  OAuth2), storing nothing, and flags a host the domain's autoconfig or SRV names outside the
  domain. Adding an account on the *Accounts* page starts from the address:
  *Find settings* fills the servers and login from it and says where they came from, asking first
  when a server is outside the domain; when nothing is found it offers the presets, and the
  settings can always be entered by hand
  ([`requirements/mail-client.md`](requirements/mail-client.md) — *Account autoconfiguration*).
  The API gives each account's storage and message quota as its server reports it (IMAP `QUOTA`),
  apart from the listing; the *Accounts* page shows it as a bar per account, warning from 90% and
  saying *Over quota* past the limit
  ([`requirements/mail-client.md`](requirements/mail-client.md) — *Mailbox quota*).
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Mail accounts*.
- Where the instance reads and sends mail (mail or MCP), each user sets their own **mail limits**
  on their *Profile* page: messages per hour, recipients per message, attachments per message (at
  most 25 MB), the window a message's text is read in and the size of a message moved to another
  account (at most 50 MB). They hold for the web and MCP alike,
  and no MCP tool changes them. Until changed, mailroom-core's defaults apply.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Mail accounts*.

## Editions

**Maturity: Done** — the API, the worker and the web app run and show only an instance's
features.

- An instance chooses its [features](GLOSSARY.md) with `MAILROOM_FEATURES`, any of `mail` (the
  [mail client](#mail-client)), `mcp` (the [MCP server](#mcp-server)) and `filters` (the
  [filters](#filters-ui) and the worker that runs them; the worker also keeps the mail client's
  index), comma separated. **Unset means all
  three**, so a deployment that never set it keeps everything; **empty means none**, the base
  only. A name that is no feature stops the API and the worker from starting.
- **The base is always there:** users and sign-in (password and SSO), accounts and their sealed
  secrets, the web app's *Profile*, *Accounts* and *Users* screens, and Postgres.
- **A feature that is off runs nothing:** no routes, no worker, no screens. Without `filters`,
  accounts have no *Run filters* switch and the MCP server serves no `filters_*` tool group.
  Without `mcp`, there is no `/mcp`, no authorization server, no `/connect` consent screen and no
  connected apps. With neither `mail` nor `mcp` nothing sends, and accounts show no send mode. A
  screen that is off says so instead.
- **The MCP mail tools do not need the mail client:** an instance with `mcp` alone serves the
  mail tool groups, in place of mailroom-mcp.
- **Turning a feature off keeps its data.** Rule sets, and runs already queued, wait until
  `filters` is back. Connected apps cannot be revoked while `mcp` is off, and their unexpired
  grants work again when it comes back: revoke them first. (`MAILROOM_MCP_AUTH=off` with `mcp`
  on turns off only `/mcp`; connected apps can still be revoked.) Personal API tokens are listed
  and revoked on every instance, and work on the REST routes it has.

How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
*Editions*.

## Mail client

**Maturity: Partial** — slice A (tier 1, read: REST, web and the `mail_read` MCP tools),
slice B (tier 2 triage: move, archive, trash, flag, multi-select, shortcuts; REST, web and the
`mail_organize` MCP tools) and slice C (compose, drafts and send: REST, web and the `mail_draft`
and `mail_send` MCP tools) and folder management (create, rename and delete, the folders as a
tree in the sidebar; REST, web and the `mail_folders` MCP tools), as specified in
[`requirements/mail-client.md`](requirements/mail-client.md), are in; slice C was also tried by hand in production (a send to oneself, Gmail drafts, a 25 MB
attachment through the host's proxy). The index is in (settings, the worker's sync, the app's
own writes, and lists, searches and counts read from it), kept fresh over IMAP IDLE on INBOX and
the folder being viewed, its changes pushed as server-sent events
([`requirements/mail-client.md`](requirements/mail-client.md) — *Live updates*), which the web
client's counts, lists, flags and indexing line follow, and desktop notifications of new mail
in the folders each user chooses (*Desktop notifications* there). A
[search syntax](requirements/mail-client.md#search-syntax) (`from:ada is:unread`) is taken by MCP's
`messages_search` and the web client's search box, which shows its terms as chips; quick filters
(unread, starred, with attachments) narrow the list shown.
[Views](requirements/mail-client.md#views) (*All inboxes* and the user's saved views: folders of any
accounts, or a search) are read from the index and kept in the web sidebar, where a search is saved
as one. A list, search or view read from the index can come
[grouped by conversation](requirements/mail-client.md#conversations), as the web shows it unless the
user turns that off: a conversation is a row, read as a thread and acted on whole. The web list is
virtualised and reads its next page as it is scrolled, so a large folder stays smooth. Send is
held on the server for the user's [Undo](requirements/mail-client.md#undo-send) delay, and the
worker sends it.
Target is
**between tier 2 and tier 3**: tiers 1 and 2 in full, on top of
the index and live updates; tier 3 items arrive one at a time.

| Tier | Scope |
| --- | --- |
| **1 — Read** | Accounts and folders with unread counts; message list (threaded or flat) with paging; read a message: headers, text and **sandboxed HTML** body (HTML first, or the text for a sender the user reads as text; in a dark theme adapted to it, its original colours one click away), attachments (download), print (a printable page) and the `.eml` source (download), remote content blocked with a per-message reveal, or shown at once for a sender the user chose whose DMARC passed. Search. |
| **2 — Triage and compose** | *Triage (slice B, in):* flag/unflag (a row's star too), mark To sort or sorted (a row's mark too), read/unread, move (to another of the user's accounts too, its originals to Trash or, on request, deleted for good once copied), archive, trash (never expunge), multi-select, keyboard shortcuts for triage. *Compose (slice C, in):* compose new, reply, reply-all (quoting only the text selected in the message, when there is some), forward in plain text or rich text (HTML with inline images, sanitised on the server; the web composer's TipTap editor writes it by default, with a per-message switch to plain text), with identity (`From`) choice and each identity's signature, recipients suggested from the index as they are typed, attachments and drafts always kept in the server's `\Drafts`; a designed message built elsewhere (an .eml, or HTML and its files) imported as a draft as it was built, sanitised, previewed at a phone's and a desktop's width, light and dark, checked and sent as a test to the user's own address before it is sent. The **send mode** (mailroom-core's send gate) limits what agents may send; the user's own *Send* works unless the mode is `off`; the hourly limit and recipient cap bind both. No automatic retry delivers a send twice (only the user's *Send anyway*, after an unconfirmed delivery, can), and one whose delivery cannot be confirmed says so instead of retrying. |
| **2+ — Index and live updates** | A **local index** of every account and folder (metadata plus rendered text) for fast cross-account search and lists; kept fresh by the worker over **IMAP IDLE** (and periodic resync), pushed to the browser (SSE or WebSocket). |
| **3 — Later** | Contacts, calendar invites, offline reading, PGP/S/MIME; unified inbox views and saved searches ([views](requirements/mail-client.md#views)), [send later](requirements/mail-client.md#send-later), [snooze](requirements/mail-client.md#snooze) and the [vacation reply](requirements/mail-client.md#vacation-reply) are in. |

## Filters UI

**Maturity: Done** — as specified in [`requirements/filters.md`](requirements/filters.md).

- Per account, **rule sets** of filters, one folder each, stored through the filter engine's own
  rule serialisation, triggered on new mail, on a schedule or by hand, optionally
  for marked mail only (the star or To sort).
- **Rule sets for all accounts**, on INBOX of each account whose Run filters is on; a move to a
  folder an account lacks fails there and is reported in its run.
- A filter can **move mail to a folder of another of the user's accounts**, picked in the editor;
  the target account's own filters leave those copies alone.
- **Dry run** against real mail before enabling: which messages each filter would match and what it
  would do.
- **Filters drafted** from a message (its list or sender) or from a search, in the mail client.
- Each filter shows how many messages it acted on in the last 30 days, and when it was last edited.
- **Report spam** moves a message to Junk and offers, never adds unasked, a filter sending its
  sender's mail there; its rule set and Run filters are turned on only on the user's word.
- **Mute a conversation** from a message: its new messages in INBOX arrive marked read, by the
  account's hidden mute rule set, while the account's Run filters is on, as the page says; the
  Filters page lists them to unmute
  ([`requirements/filters.md`](requirements/filters.md#muted-threads)).
- The worker runs them through the filter engine's runner, only for accounts whose user turned
  **Run filters** on (off by default), one run at a time per account; each run is kept in
  the rule set's history.

## Filter suggestions (learning)

**Maturity: Planned**

- Observes each user's manual moves (and other triage) and proposes **filters** that would have done
  the same: the rule, the evidence (the messages it is based on), and a dry-run preview.
- **Suggestions only, never acting.** A suggestion does nothing until its user accepts it, which
  creates an ordinary filter they can edit; dismissing one is remembered. Nothing learned ever
  moves, flags, deletes or sends mail, or enables a filter, by itself.
- Learning runs locally; no mail leaves the host for it.

## MCP server

**Maturity: Partial** — as specified in [`requirements/mcp.md`](requirements/mcp.md): `/mcp`,
the `filters_*`, `mail_read`, `mail_organize`, `mail_draft`, `mail_send`, `mail_folders` and
`audit_read` tools, `mail_transfer` in preview, the built-in
authorization server with its `/connect` consent screen, connected apps on the profile and external issuer mode are in.

- The app's own MCP server at `/mcp`, for each user's own data: accounts, folders, rule sets,
  dry runs and runs, in [tool groups](GLOSSARY.md) chosen by instance config, each client
  using only the groups its user approved for it, and only the accounts and the
  [send ceiling](GLOSSARY.md) when the user limited it. Mail tools come
  group by group ([`requirements/mcp.md`](requirements/mcp.md)), `mail_read` with the first
  slice of the [mail client](requirements/mail-client.md), `mail_organize` with the second,
  `mail_draft` and `mail_send` with the third, and `mail_folders` with folder management. An
  agent sends only as far as the account's [send mode](GLOSSARY.md) and its send ceiling allow, and a send that is not confirmed is never repeated. It
  attaches parts of existing messages or new files, within the user's limit
  ([*Files from agents*](requirements/mcp.md#files-from-agents)).
- **Nothing turns on without the user's say:** a rule set created over MCP starts disabled, an
  enabled one cannot be edited (but for a folder rename pointing it at the folder's new path:
  [`requirements/mcp.md`](requirements/mcp.md)), and enabling, *Run now* and the *Run filters* switch are separate
  tools marked destructive, so the client asks first.
- **OAuth:** a built-in authorization server by default (sign in with a Mailroom password or SSO,
  then approve the client; or, on the instance's say-so, through SSO only, for every connection),
  or tokens from an external issuer such as Authentik; each approval is
  a [connected app](GLOSSARY.md) the user can revoke.
- **Personal API tokens:** a user makes a [token](GLOSSARY.md) on their profile for a script or a
  command-line client, with the tool groups, accounts and send ceiling an app would get, and an
  expiry if they like; it is shown once and revoked there. It works on the REST API, where it
  reaches what the tool groups cover, and on `/mcp`; account settings, sign-in, users, sessions
  and tokens themselves are the signed-in user's only.
- **Web SSO** over OIDC for users an admin created, through a [linked sign-in](GLOSSARY.md);
  nobody signs up through it.

## Your data

**Maturity: Done** — the exports; reading a rule sets' export back is not built.

The profile's *Your data* downloads, for the signed-in user alone (or a script, with a personal API
token):

- every [rule set](GLOSSARY.md) with its filters, as JSON, its accounts named by login
  ([`requirements/filters.md`](requirements/filters.md#export));
- the [audit trail](#audit-trail), whole or of some days, as CSV
  ([`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
  *Audit trail*).

## Audit trail

**Maturity: Done** — records, the web's *Activity* page and MCP's `audit_list`.

- Every write to a user's mail leaves an [audit record](GLOSSARY.md) per message: a move,
  archive, trash, flag, draft or send from the web or an agent, and what a filter run did,
  refused attempts included; a move to another account leaves one in each account. It says who acted (the user, an agent and its connected app or API token, or a
  rule set), what, and how it ended; never a subject or a body. The web's draft saves are left
  out, being the user typing.
- Records outlive a removed account, go with their user, and are kept 90 days unless the instance says otherwise.
- The user reviews their own trail on the *Activity* page, newest first, filtered by account, who
  acted, the app (a connected app or API token), action and result, each kept in the URL; every
  connected app and API token on the profile links to its own, as does a record to its app's. An
  agent can list it through MCP.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Audit trail*.

## Non-functional requirements

[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) — stack,
security (HTML mail sandbox, CSP, remote content), migrations, logging, testing, i18n.
[`requirements/ci-cd.md`](requirements/ci-cd.md) — Git workflow, CI gates, versioning.

## Out of scope

See [CONTEXT.md](CONTEXT.md) — *Non-goals*.
