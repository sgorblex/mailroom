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
| **Rule set** | An ordered list of filters for one account folder, with one trigger. [`requirements/filters.md`](requirements/filters.md). |
| **Filter** | An imap-thingy rule (criteria plus actions) that sorts mail on the server, stored as its imap-thingy spec and run by imap-thingy's runner in the app's worker. |
| **Trigger** | What starts a rule set's run: `new_mail` (IMAP IDLE), `scheduled` (every so many minutes) or `manual` (only *Run now*). |
| **Run filters** | The per-account switch, off by default, without which the worker runs no rule set of that account. |
| **Dry run** | Evaluating a rule set against real mail and reporting what each filter would match and do, changing nothing. |
| **Suggestion** | A filter proposed from a user's own triage, with its evidence; inert until that user accepts it. |
| **Profile** | An MCP connector scope (`/mcp/<profile>`): which tool groups and accounts a client gets. mailroom-mcp's term. |
| **Worker** | The app's background process: IDLE, indexing, filter runs, learning. |
