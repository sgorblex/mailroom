# Glossary

| Term | Meaning |
| --- | --- |
| **User** | A person who signs in to Mailroom. Their accounts, filters and suggestions are theirs alone. |
| **Admin** | A user who may also add and remove users and reset their passwords. The first user is one, made with `mise run create-user <name> --admin`. |
| **User session** | One browser signed in as a user: a random token in the `mailroom_session` cookie, valid until it expires or the user signs out there. |
| **Account** | One mailbox a user reaches over IMAP (read) and SMTP (send), with its identities and send mode. Modelled by mailroom-core. |
| **Identity** | A `From` address an account may send as. |
| **Send mode** | Per account: `off`, `drafts`, `approval`, `direct` — what "send" is allowed to mean (mailroom-core's send gate). |
| **Index** | The app's local copy of message metadata and rendered text across every account and folder, for lists and search without a round of IMAP per query. |
| **Live update** | A change on the server (new mail, a flag or move from another client) reaching the open web app without a reload, via the worker's IMAP IDLE. |
| **Ref** | An opaque string naming one message exactly (account, folder, UIDVALIDITY, UID), as in mailroom-mcp; stale once the folder's UIDVALIDITY changes. [`requirements/mail-client.md`](requirements/mail-client.md). |
| **Remote content** | Images a message loads from the internet (tracking pixels among them): blocked, and revealed per message on request ([`requirements/mail-client.md`](requirements/mail-client.md)). |
| **Message view** | A message as the client shows it: headers, the text body, attachments, and the HTML view when the message has an HTML body. |
| **Rule set** | An ordered list of filters for one account folder, with one trigger. [`requirements/filters.md`](requirements/filters.md). |
| **Filter** | An imap-thingy rule (criteria plus actions) that sorts mail on the server, stored as its imap-thingy spec and run by imap-thingy's runner in the app's worker. |
| **Trigger** | What starts a rule set's run: `new_mail` (IMAP IDLE), `scheduled` (every so many minutes) or `manual` (only *Run now*). |
| **Run filters** | The per-account switch, off by default, without which the worker runs no rule set of that account. |
| **Dry run** | Evaluating a rule set against real mail and reporting what each filter would match and do, changing nothing. |
| **Suggestion** | A filter proposed from a user's own triage, with its evidence; inert until that user accepts it. |
| **Profile** | A user's own page for their password, linked sign-ins and connected apps. (In mailroom-mcp, a connector scope `/mcp/<profile>`; the app has none: its tool groups are instance config.) |
| **Tool group** | A named set of MCP tools (`filters_read`, `filters_write`, `filters_run`, later the `mail_*` ones) with a status (`stable`, `preview`, `planned`); `MAILROOM_MCP_TOOL_GROUPS` picks which are served. [`requirements/mcp.md`](requirements/mcp.md). |
| **Connected app** | An MCP client a user approved through the built-in OAuth server (an OAuth grant), listed and revocable on their profile. |
| **DCR** | Dynamic client registration (RFC 7591): an MCP client registering itself at `/oauth/register`, limited to the redirect allowlist. [`requirements/mcp.md`](requirements/mcp.md). |
| **CIMD** | Client ID metadata document: an MCP client whose `client_id` is an https URL on an allowed host, serving its own metadata, which the built-in OAuth server fetches and caches instead of a registration. |
| **Authorization request** | An MCP client's `/oauth/authorize` call, stored until a signed-in user approves or denies it on `/connect`; the first user to open it claims it. |
| **Linked sign-in** | An identity at the SSO provider (issuer and subject) tied to one user, so they can sign in through SSO and, in external mode, use that provider's MCP tokens. |
| **Worker** | The app's background process: IDLE, indexing, filter runs, learning. |
