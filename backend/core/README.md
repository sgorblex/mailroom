# mailroom-core

Mailroom's mail layer: accounts and their sealed secrets, IMAP and SMTP, the send gate,
rendering and audit. It is shared by the mailroom-mcp server and the Mailroom web app, and
developed in the [mailroom-mcp](https://github.com/sgorblex/mailroom-mcp) repository
(`packages/core`).

Its Postgres tables live in schema `core`, with their own Alembic history:
`mailroom_core.store.migrate(engine)` brings it up to date in the host's database.

Its API is synchronous: `mailroom_core.mail.MailConnections` holds the IMAP connections, and an
async host calls it from a worker thread. Over them, `list_messages`, `search_messages`, `thread_page`,
`read_message`, `read_attachment` and `html_view` read mail and return typed results.
`move_messages`, `trash_messages`, `archive_messages` and `set_flags` organize it, returning
per-folder results and per-message audit items. `compose` builds a message from a `MessageInput`,
and `recipient_list`, `from_identity`, `original_message`, `reply_recipients` and
`attachments_from` give it what it takes. `save_draft`, `read_draft`, `find_drafts`, `draft_source`
and `remove_draft` keep drafts in the account's Drafts folder.

`mailroom_core.testing` is a pytest plugin, loaded wherever the package is installed: a
no-network guard, the IMAP, SMTP and test-database fixtures, and helpers to seed the IMAP test
server (`mailroom_core.testing.imap`). Install the `testing` extra for it.
