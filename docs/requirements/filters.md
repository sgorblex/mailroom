# Filters

The filters feature: [rule sets](../GLOSSARY.md) of filters per account folder, edited
in the browser, tried with a dry run, and run by the worker. Product scope:
[`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Filters UI*.

## Behaviour

- **Run filters**, per account, is **off by default**. Until the user turns it on, the worker runs
  nothing for that account: no trigger fires and *Run now* is refused. Editing, enabling a rule
  set and dry runs work either way.
- A **rule set** belongs to one account and one folder, and has a **trigger**:
  - `new_mail` — runs when the folder gets new mail (IMAP IDLE);
  - `scheduled` — runs every `interval_minutes`;
  - `manual` — runs only on *Run now*.

  Any enabled rule set can also be run now. **Starred only** restricts a rule set to starred
  (`\Flagged`) messages, which it unstars once handled (the filter engine's `starred_only`).
- A rule set holds **ordered filters**, each a filter spec (criteria plus actions), stored as
  the [filter engine](../GLOSSARY.md) serialises it and validated with its `load_filter`, its JSON
  Schema, and every `pattern` compiled as a regular expression (with `regex`, which the engine
  matches with). A rule set holds at most 100 filters. Every action of the engine is
  allowed, `delete` (*Delete for good*) too: a filter is the one place Mailroom removes mail
  permanently, because the user wrote it, saw its dry run and enabled it (on a server with
  UIDPLUS, which expunges exactly those messages; elsewhere the action fails `unsupported`).
  **Moves are the mail client's moves**: `move_to` and `trash` need a folder of the account
  (`mailbox_missing` otherwise), never move into Drafts or Sent (`invalid_argument`), and without
  MOVE copy then expunge exactly the copied messages; a move into the folder the rule set runs on
  is skipped. A filter never matches a message marked deleted (`\Deleted`), so an original that
  a move copied but could not remove is not copied again; and a run whose move copied messages
  it could not remove turns its rule set off, with the error `copied_not_removed` in its history,
  until the user has checked both folders and enables it again. `trash` without a folder needs
  the account's special-use Trash (`unsupported` without one). Filters run in order; a disabled filter is skipped, by dry runs too. A disabled rule set never runs (no
  trigger fires, *Run now* is refused) but can still be dry-run; a new rule set starts disabled.
- **Dry run** evaluates the rule set against the folder's real mail and reports, per filter, the
  messages it matches (uid, from, subject, date) and what it would do, changing nothing (the folder
  is selected read-only). Each filter is evaluated against the folder as it is, without the
  earlier filters applied, so a dry run may over-report: a message an earlier filter would move
  can show up under a later one too. It looks at the folder's newest 500 messages, for 30 s at
  most, on one of the user's two API connections to the account: past that, the pool cuts that
  user's connections to the account in use, and the dry run fails `dry_run_timeout`. A user runs
  one dry run at a time; another one meanwhile is refused.
- **Patterns are matched under a timeout**: the filter engine stops a match that takes longer than its
  match timeout (1 s on one message), and the run — dry or not — fails with the error code
  `match_timeout` in its history.
- **Run history**: every run — on new mail, scheduled, run now or dry — records its trigger
  (`manual` for run now and dry runs), start and end, the report (what each filter matched and
  did) and any error. A real run also writes to the user's audit trail: a record per message a
  filter acted on, as the report lists it, the subject only as its hash
  ([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Audit trail*).
- **One run at a time per account.** The worker never runs two runs on the same account at once;
  a run waits for the one in progress. Dry runs, which change nothing, are not held back.
- **The worker** checks *Run filters* and the rule set's *enabled* again when a run starts; a run
  refused then is recorded with its error. A run's mailbox failure is recorded as
  `<code>: <message>`, mailroom-core's code and fixed message (the app's `password_missing` and
  `secrets_key_missing` aside). A `new_mail` rule set also runs each time the worker
  (re)connects to its folder, to catch mail that arrived meanwhile, so it runs once at worker
  start. A failed run keeps the report of the filters that finished. A run's report keeps the
  first 200 matches per filter, with the total. A run cut short by the worker stopping is closed
  as `interrupted` when the worker starts again.

### Folders renamed or deleted

A rule set names a folder by its own `folder`, a filter by the `folder` of a `move_to` or
`trash` action anywhere in its spec (in a `seq` at any depth); either names the folder and every
folder under it. Segments compare exactly, but for a leading `INBOX`, in any case
(`mailroom.filters.folders`). When the user renames or moves a folder in the mail client
([`mail-client.md`](mail-client.md) — *Folder management*):

- **A rename** points every such rule set and filter action of the account at the new path, in
  one transaction, **enabled rule sets included**: they keep acting on the same folder, so this
  is no edit of what runs, and it is made even where an edit needs the rule set disabled first
  (the MCP write tools). A `new_mail` rule set's IDLE watch moves to the new path at the worker's next reconcile
  (30 s). A run going on meanwhile may fail `mailbox_missing`. When the database step fails, the
  folder is renamed and the rule sets still name the old path: the answer says so
  (`rule_sets_updated: false`) and the user points them again.
- **A delete** is refused while any rule set or filter action of the account names the folder
  or one under it (`folder_in_use`, naming them), so that no rule set is left on a folder that
  is gone. A rule set created between that check and the delete, or a folder deleted by another
  mail client, leaves a rule set naming a missing folder: its runs fail `mailbox_missing`.

## Data model

Schema `app`; every row belongs to the user who owns the account.

| Table | Columns |
| --- | --- |
| `rule_set` | `id`, `user_id`, `account_id`, `folder` (`text[]`, path segments), `name`, `trigger` (`new_mail` \| `scheduled` \| `manual`), `interval_minutes` (`scheduled` only), `starred_only`, `enabled`, timestamps |
| `filter` | `id`, `rule_set_id`, `position`, `name`, `enabled`, `spec` (`jsonb`, a filter spec), timestamps |
| `filter_run` | `id`, `rule_set_id`, `trigger`, `created_at`, `started_at` (null while queued), `finished_at`, `dry_run`, `result` (`jsonb`, the filter engine's run report), `error` |

The per-account toggle is `account_filter_settings(user_id, account_id, run_filters bool default
false)`.

## API

Under `/api/v1`, signed-in user's own data only (another user's id is a 404).

| Route | Does |
| --- | --- |
| `GET /accounts/{id}/folders` | An account's folders, for the folder picker (`[{"path": [segments]}]`) |
| `GET`, `PUT /accounts/{id}/filter-settings` | Read, set *Run filters* (`{"run_filters": bool}`) |
| `GET /filters/schema` | The filter spec's JSON Schema (`json_schema()`) |
| `GET`, `POST /accounts/{id}/rule-sets` | List, create an account's rule sets |
| `GET`, `PATCH`, `DELETE /rule-sets/{id}` | Read (with its filters), edit, remove a rule set |
| `POST /rule-sets/{id}/filters` | Add a filter (at the end) |
| `PATCH`, `DELETE /filters/{id}` | Edit, remove a filter |
| `PUT /rule-sets/{id}/order` | Reorder: the rule set's filter ids, in the new order |
| `POST /rule-sets/{id}/dry-run` | Dry run, synchronous and bounded; returns the run, with its report |
| `POST /rule-sets/{id}/run` | Run now: enqueues a run, `202` with the queued run |
| `GET /rule-sets/{id}/runs` | Run history, newest first |

An unknown or foreign id is a `404` with a plain `detail` (`"No such rule set"`). Refusals answer
`{"detail": {"code": …, "message": …}}`:

- `400` `invalid_spec`, carrying the filter engine's `SpecError` text, the schema's or the regular
  expression's complaint;
- `400` `invalid_rule_set` (an `interval_minutes` exactly when `scheduled`), `invalid_order`;
- adding a filter: `400` `too_many_filters` when the rule set already holds 100;
- *Run now*: `400` `run_filters_off` while the account's *Run filters* is off, `400`
  `rule_set_disabled`, `409` `run_pending` while a run of that rule set is still waiting;
- dry run: `404` `account_not_found`, `400` `invalid_spec` for a stored spec the filter engine no
  longer loads, `400` `match_timeout` when a pattern takes too long on a message, `504`
  `dry_run_timeout`, `429` `dry_run_busy` while another dry run of the user's is still going (not
  recorded), `500` `run_failed: <exception type>` for anything else (its text is neither kept nor
  logged), and mailbox failures as for the account's folder listing (mailroom-core's codes, as
  the mail client's: `auth_failed` 409, `mailbox_missing` 404 for a folder that is not there,
  `unreachable` 502, …).
