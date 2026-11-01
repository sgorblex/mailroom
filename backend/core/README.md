# mailroom-core

Mailroom's mail layer: accounts and their sealed secrets, IMAP and SMTP, the send gate,
rendering and audit. It is shared by the mailroom-mcp server and the Mailroom web app, and
developed in the [mailroom-mcp](https://github.com/sgorblex/mailroom-mcp) repository
(`packages/core`).

Its Postgres tables live in schema `core`, with their own Alembic history:
`mailroom_core.store.migrate(engine)` brings it up to date in the host's database.

Its API is synchronous: `mailroom_core.mail.MailConnections` holds the IMAP connections, and an
async host calls it from a worker thread.

`mailroom_core.testing` is a pytest plugin, loaded wherever the package is installed: a
no-network guard, the IMAP, SMTP and test-database fixtures, and helpers to seed the IMAP test
server (`mailroom_core.testing.imap`). Install the `testing` extra for it.
