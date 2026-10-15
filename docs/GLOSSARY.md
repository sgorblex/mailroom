# Glossary

| Term | Meaning |
| --- | --- |
| **Owner** | The single person Mailroom belongs to. There are no other users. |
| **Account** | One mailbox the owner reaches over IMAP (read) and SMTP (send), with its identities and send mode. Modelled by mailroom-core. |
| **Identity** | A `From` address an account may send as. |
| **Send mode** | Per account: `off`, `drafts`, `approval`, `direct` — what "send" is allowed to mean (mailroom-core's send gate). |
| **Index** | The app's local copy of message metadata and rendered text across every account and folder, for lists and search without a round of IMAP per query. |
| **Live update** | A change on the server (new mail, a flag or move from another client) reaching the open web app without a reload, via the worker's IMAP IDLE. |
| **Filter** | An imap-thingy rule (criteria plus actions) that sorts mail on the server, run by imap-thingy's runner (in the app's worker, or in the owner's filterd daemon). |
| **Dry run** | Evaluating a filter against real mail and reporting what it would do, changing nothing. |
| **Suggestion** | A filter proposed from the owner's own triage, with its evidence; inert until the owner accepts it. |
| **Profile** | An MCP connector scope (`/mcp/<profile>`): which tool groups and accounts a client gets. mailroom-mcp's term. |
| **Worker** | The app's background process: IDLE, indexing, filter runs, learning. Not scaffolded yet. |
