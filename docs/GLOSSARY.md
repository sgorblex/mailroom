# Glossary

| Term | Meaning |
| --- | --- |
| **User** | A person who signs in to Mailroom. Their accounts, filters and suggestions are theirs alone. |
| **Admin** | A user who may also add and remove users and reset their passwords. The first user is one, made with `mise run create-user <name> --admin`. |
| **User session** | One browser signed in as a user: a random token in the `mailroom_session` cookie, valid until it expires or the user signs out there. |
| **Account** | One mailbox a user reaches over IMAP (read) and SMTP (send), with its identities and send mode. Modelled by mailroom-core. |
| **Audit record** | What one write did, or tried to do, to one message: who acted (the user, an agent and its app, or a filter), the action, the result and the message's identifying fields, never its subject or body. [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) — *Audit trail*. |
| **Identity** | A `From` address an account may send as. |
| **Send mode** | Per account: `off`, `drafts`, `approval`, `direct` — what an **agent** may do when it sends (mailroom-core's send gate): `off` refuses it and the draft tools, `drafts` saves a draft instead, `approval` sends only if every recipient is auto-approved and otherwise saves the whole message as a draft, `direct` sends. The user's own *Send* in the web client overrides it, except `off`. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Send gate** | mailroom-core's single path to SMTP: it checks the send mode (and who is acting, user or agent), the recipients and the limits, counts the send before it is made, and hands out the only token that `deliver` accepts. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Draft** | A message in the account's `\Drafts` folder. The web client always composes through one; saving makes a new version and then removes the old one. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Index** | The app's local copy of message metadata and rendered text across every account and folder, for lists and search without a round of IMAP per query. |
| **Live update** | A change on the server (new mail, a flag or move from another client) reaching the open web app without a reload, via the worker's IMAP IDLE. |
| **Ref** | An opaque string naming one message exactly (account, folder, UIDVALIDITY, UID), as in mailroom-mcp; stale once the folder's UIDVALIDITY changes. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Remote content** | Images a message loads from the internet (tracking pixels among them): blocked, and revealed per message on request ([`requirements/mail-client.md`](requirements/mail-client.md)). |
| **Archive** | The account's folder with role `archive`; archiving moves messages there. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Folder** | A mailbox of an account, named by its path: segments joined by the account's hierarchy delimiter (`Archive/2026`); a rule set stores the segments. INBOX and the folders with a role (Sent, Drafts, Trash, Junk, Archive…) are the account's own; the user creates, renames and deletes the others ([`requirements/mail-client.md`](requirements/mail-client.md#folder-management)). A `\Noselect` folder only holds others. |
| **Trash** | Moving messages to the account's folder with role `trash`. Never a permanent delete: trashing does not delete or expunge mail. Mailroom deletes only deliberately: a filter's `delete`, a folder's delete, and the expunge of a draft Mailroom wrote ([`requirements/mail-client.md`](requirements/mail-client.md#the-own-draft-exception)). |
| **Message view** | A message as the client shows it: headers, the text body, attachments, and the HTML view when the message has an HTML body. |
| **Rule set** | An ordered list of filters for one account folder, with one trigger. [`requirements/filters.md`](requirements/filters.md). |
| **Filter** | A rule (criteria plus actions) that sorts mail on the server, stored as its spec and run by the filter engine in the app's worker. |
| **Filter engine** | `mailroom_core.filters`: criteria, actions, rules as data (specs), the runner and the IDLE monitor. It was imap-thingy, a library of its own on PyPI, until 2026-11-11; [`core.md`](core.md) — *Filters*. Not the app's `mailroom.filters`, the feature built on it. |
| **Trigger** | What starts a rule set's run: `new_mail` (IMAP IDLE), `scheduled` (every so many minutes) or `manual` (only *Run now*). |
| **Run filters** | The per-account switch, off by default, without which the worker runs no rule set of that account. |
| **Dry run** | Evaluating a rule set against real mail and reporting what each filter would match and do, changing nothing. |
| **Suggestion** | A filter proposed from a user's own triage, with its evidence; inert until that user accepts it. |
| **Profile** | A user's own page for their password, linked sign-ins and connected apps. (In mailroom-mcp, a connector scope `/mcp/<profile>`; the app has none: its tool groups are instance config.) |
| **Tool group** | A named set of MCP tools (`filters_read`, `filters_write`, `filters_run`, `mail_read`, `mail_organize`, `mail_folders`, `mail_draft`, `mail_send`, `audit_read`) with a status (`stable`, `preview`, `planned`); `MAILROOM_MCP_TOOL_GROUPS` picks which are served. A tool several groups need (`accounts_list`, `folders_list`) is served when any of them is. [`requirements/mcp.md`](requirements/mcp.md). |
| **Connected app** | An MCP client a user approved through the built-in OAuth server (an OAuth grant), listed and revocable on their profile. |
| **DCR** | Dynamic client registration (RFC 7591): an MCP client registering itself at `/oauth/register`, limited to the redirect allowlist. [`requirements/mcp.md`](requirements/mcp.md). |
| **CIMD** | Client ID metadata document: an MCP client whose `client_id` is an https URL on an allowed host, serving its own metadata, which the built-in OAuth server fetches and caches instead of a registration. |
| **Authorization request** | An MCP client's `/oauth/authorize` call, stored until a signed-in user approves or denies it on `/connect`; the first user to open it claims it. |
| **Linked sign-in** | An identity at the SSO provider (issuer and subject) tied to one user, so they can sign in through SSO and, in external mode, use that provider's MCP tokens. |
| **Feature** | One of the optional parts an instance runs on top of the base (users, sign-in, accounts, the settings screens): `mail` (the mail client), `mcp` (the MCP server) and `filters`. `MAILROOM_FEATURES` lists them. [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Editions*. |
| **Edition** | The set of features one instance runs, from the base alone to all three. |
| **Worker** | The app's background process: IDLE, indexing, filter runs, learning. |
