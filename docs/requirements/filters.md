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

  Any enabled rule set can also be run now. **Only marked** restricts a rule set to the messages
  with its **marker**, which it clears once handled (the filter engine's `marked_only`): the star
  (`\Flagged`) or [**To sort**](../GLOSSARY.md) (the keyword `$MailroomSort`); `none`, the
  default, is every message. To sort is taken only on an account whose server keeps keywords
  (the accounts listing's `keywords`): elsewhere the web greys it out with the reason, and the API
  and MCP refuse it (`to_sort_unsupported`), checked when a rule set is created with it or changed
  to it. The same holds for a filter's `mark_to_sort` and `unmark_to_sort` actions, checked when a
  filter is added with one or its spec changed to one; the To sort criteria are always taken
  (there they match nothing). Ticking *Only marked* in the web picks To sort where the account keeps keywords, else
  the star; the API and MCP always take the marker as given. Rule sets from before markers keep
  the star.
- A rule set holds **ordered filters**, each a filter spec (criteria plus actions), stored as
  the [filter engine](../GLOSSARY.md) serialises it and validated with its `load_filter`, its JSON
  Schema, and every `pattern` compiled as a regular expression (with `regex`, which the engine
  matches with). A rule set holds at most 100 filters. Every action of the engine is
  allowed, `delete` (*Delete for good*) too: a filter is the one place Mailroom removes mail
  permanently, because the user wrote it, saw its dry run and enabled it (on a server with
  UIDPLUS, which expunges exactly those messages; elsewhere the action fails `unsupported`).
  **What a criterion reads**: the first of each address field, the subject as the reader sees it
  (whitespace collapsed), and as the body every inline plain-text part, else the HTML's visible
  text (no tags, styles or images; links as `[text](url)`, marked like the
  message view's text when the text names another host; the first 2 million characters).
  Attached messages and named files are not the body. `older_than_days` counts calendar days
  back from today in the user's time zone (UTC until they set one), in a run and a dry run alike.
  The `*_is` address criteria compare the whole address, ignoring case; the `*_contains` criteria
  ignore case too. `body_contains` alone is the server's search of the whole body; combined with
  a criterion that needs the message fetched (a pattern, `subject_is`, an `*_is` address, or a
  negated one), it reads it as above. `from_domain` matches a From address at that domain, in any
  case and with a Unicode domain matching its `xn--` form; its subdomains only with
  `incl_subdomains`. A plain ASCII domain is narrowed by the server's search for `@domain`, so a
  From that writes it in look-alike Unicode characters (fullwidth letters) is not matched.
  `list_is` matches the List-Id's identifier, in any case. `in_thread` names a thread the app
  keeps (a muted one, say): it matches a message whose Message-ID, In-Reply-To or References
  holds one of the thread's Message-IDs, or on Gmail one of its Gmail thread; never by subject.
  Its spec holds only the app's name for the thread, whose ids are looked up when the filter is
  loaded: a thread the app does not know matches nothing. It is the app's own, for its hidden
  mute rule sets: left out of the schema the editor and agents are offered (`GET /filters/schema`,
  `filter_schema`), and a filter a user writes that names it anywhere is refused. These three read every candidate
  message, as a pattern does. Every header criterion reads only the first 256 KiB of the header
  block, cut at a whole field: a From, Subject, Date or any other field beyond it reads as absent.
  The Message-ID, In-Reply-To, References and List-Id read are the first of each field there,
  malformed ids left out, at most 100 References ids (the first and the newest).
  **Moves are the mail client's moves**: `move_to` and `trash` need a folder of the account
  (`mailbox_missing` otherwise), never move into Drafts or Sent (`invalid_argument`), and without
  MOVE copy then expunge exactly the copied messages; a move into the folder the rule set runs on
  is skipped. A filter never matches a message marked deleted (`\Deleted`), so an original that
  a move copied but could not remove is not copied again; and a run whose move copied messages
  it could not remove turns its rule set off, with the error `copied_not_removed` in its history,
  until the user has checked both folders and enables it again. `trash` without a folder needs
  the account's special-use Trash (`unsupported` without one); `junk` moves to the account's
  Junk folder, found by its role (special-use `\Junk`, else a well-known name; `unsupported`
  without one). Filters run in order; a disabled filter is skipped, by dry runs too. A disabled rule set never runs (no
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
- **Match counts**: each filter carries `matches_30d`, the messages it acted on in real runs over
  the last 30 days (UTC days, today included; a report's total, not only the 200 it lists), and
  `updated_at`, the date of the user's last edit of its name, spec or switch (a reorder or a
  folder rename leaves it). An edit keeps the count, which then may predate the filter as it
  reads now: the date of the edit is shown beside it. Dry runs never count. Storage:
  [`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Storage*. The web's filter
  list shows each as *N matches in 30 days* (or *No matches in 30 days*) and *edited* with the
  date, in the user's time zone.
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

### Muted threads

A **muted thread**'s new messages in INBOX arrive marked read; nothing is moved
(`mailroom.filters.mute`). Muting rides on the account's filter runs, with no exemption: it acts
only while the account's *Run filters* is on, and the mute's answer says whether it is
(`run_filters`), so the web can say that muting does nothing until it is turned on.

- **Muting** (`POST /api/v1/mail/messages/{ref}/mute`, [`mail-client.md`](mail-client.md)) reads
  the message's References, In-Reply-To and Message-ID (the first of each field, in the first
  256 KiB of those fields) and on Gmail its thread id (`X-GM-THRID`, where the server has
  `X-GM-EXT-1`); never the subject, and never marking it read. A message with none of them is
  refused, `400` `nothing_to_mute`. A thread already muted on the account that shares one of
  them, or the Gmail thread, takes the others instead of a new one; muted threads the message
  bridges become the oldest of them. Unmuting removes it. Agents do the same with MCP's
  `thread_mute`, `muted_threads_list` and `thread_unmute` (`mail_organize`,
  [`mcp.md`](mcp.md)), over the same service. Both are audit records (`mute`, `unmute`, with the
  `muted_thread_id`; a mute's the message's folder and UID, and the ids of the threads it
  `merged`), by the agent and its app when over MCP.
- **The system rule set.** The account's first muted thread makes its one hidden rule set of
  kind `mute`: on `INBOX`, `new_mail`, enabled, with one filter, `in_thread` `muted` and
  `is_unread` → `mark_as_read`. Its last muted thread removes it. A run resolves `muted` to the
  account's muted threads (their Message-IDs and Gmail threads), and each message it matched
  then joins the thread it cites one of or shares the Gmail thread of, by its Message-ID, so a
  later reply citing only that message is found: by the same run when it comes after it (an
  unread message citing one the run matched), else by the next. A run reads INBOX's newest 500
  messages only, as each new mail starts one. A thread keeps 200 ids at most: the first, then the
  newest. The run is a run as any other: its history, its audit
  records (the filter named *Muted threads*) and the worker's checks; Run filters off, nothing
  runs.
- **Hidden.** User rule sets are of kind `user`. A `mute` one is left out of every listing, and
  every route and MCP tool that names a rule set or filter answers it as one that does not exist
  (`404`): it is changed only by muting and unmuting. Removing the account or the user removes
  its muted threads and the rule set.
- **In the web** ([mail client](mail-client.md#slice-a-read)): *Mute conversation* on a message,
  and the Filters page's list of each account's muted conversations to unmute, each saying when
  Run filters is off that muting has no effect until it is on.

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

### Filter drafts

A **draft** is a filter spec the filter editor starts from; drafting saves nothing, and the spec
is checked as any other when the user adds the filter. A draft's action is `no_op`: the user
picks what it does.

- **From a message** (`GET /api/v1/mail/messages/{ref}/filter-draft`, read as the message view
  reads it, never marking it read): `criteria`, narrowest first, are the List-Id's `list_is` when
  the message has one, the sender's exact address (`from_is`), and the sender's domain
  (`from_domain`) unless it is a public mail provider's; `spec` uses the first, and `name` is the
  list's name or identifier, else the sender's name or address, on one line (200 characters at
  most). Never the subject. A message with neither a List-Id nor a From address is refused, `400`
  `nothing_to_filter`. A stale ref and the mailbox failures answer as `GET /messages/{ref}`
  ([`mail-client.md`](mail-client.md)).
- **From a search** (`POST /api/v1/filters/draft-from-search`, the body `POST /mail/search`
  takes): `from` gives `from_contains`, `to` `to_contains`, `subject` `subject_contains`,
  `unread` and `flagged` `is_unread`/`is_read` and `is_starred`/`is_unstarred`, all of them under
  one `and`. The filter criteria compare the address, where the search compares the display name
  too. `dropped` names the fields set that no criterion matches as the search does: `text`
  (headers or body), `since` and `before` (the search's arrival date, the filter's date criteria
  the Date header), `has_attachment` and `gmail_query`. The search's `account`, `folder` and
  `subfolders` are its scope, not criteria: the rule set gives the filter its folder. A `from`
  naming a public mail provider's whole domain (below) is dropped too, as `from`. A search
  with no field a criterion matches is refused, `400` `nothing_to_filter` with `dropped`.
- **Public mail providers.** A filter on a whole domain anyone can sign up at would catch
  strangers and friends alike, so drafts never offer one and saving one is refused, `400`
  `public_provider_domain`, by the REST routes and MCP's `filter_add` and `filter_update` alike,
  anywhere in the criterion (under `not`, `and` or `or` too): a `from_domain` on it, and a
  `from_contains` or `from_matches` whose value is the domain or one under it, with or without a
  leading `@` (a pattern read as plain text: anchors, a leading `.*` and the backslashes before
  `.`, `@` and `-` left out). An address at the provider (`maria@gmail.com`) is no domain and is
  kept. An edit whose spec is the filter's stored one (both normalised) is not checked, so a
  filter saved before the rule keeps being renamed and switched; a changed spec is. The list is the app's own
  (`mailroom.filters.providers`), compared in IDNA form with subdomains: single-domain providers
  (`gmail.com`, `googlemail.com`, `icloud.com`, `me.com`, `mac.com`, `proton.me`,
  `protonmail.com`, `web.de`, `libero.it`, `virgilio.it`, `aol.com`, `mail.ru`, `fastmail.com`,
  `zoho.com` and the like) and the per-country families `hotmail`, `live`, `outlook`, `yahoo`,
  `gmx` and `yandex` under any one- or two-label suffix (`hotmail.it`, `yahoo.co.uk`). With
  `incl_subdomains`, a country's shared second level that holds them (`co.uk`, `com.br`) is
  refused too.
- **In the web**, *Filter messages like this* (beside Print, in a row's context menu and the
  command palette), the mailing list row's *Filter instead* and the search form's *Create filter
  from this search* open `/filters/new`, which keeps the message's ref and account or the search
  in its URL as the mail screen does. It shows the filter editor started from the draft, a
  message's `criteria` to match on instead, and the search's `dropped` fields named as left out.
  The filter goes into a rule set of the account's `INBOX`, one it has (each saying whether it is
  enabled, an enabled one acting on new mail at once) or a new one, made `new_mail` and disabled
  as any new rule set; a search over all accounts asks which. Adding the filter opens its rule
  set; a rule set made when adding the filter then failed is the one the next try adds to.
- **After a report of spam** the mail screen offers a `from_is` → `junk` filter on the sender
  ([mail client](mail-client.md#web-client)), added only on the user's word, and turns its rule set
  or Run filters on only by a button of their own.

## Data model

Schema `app`; every row belongs to the user who owns the account.

| Table | Columns |
| --- | --- |
| `rule_set` | `id`, `user_id`, `account_id`, `folder` (`text[]`, path segments), `name`, `trigger` (`new_mail` \| `scheduled` \| `manual`), `interval_minutes` (`scheduled` only), `marker` (`none` \| `star` \| `to_sort`), `enabled`, `kind` (`user` \| `mute`, one `mute` per account at most), timestamps |
| `filter` | `id`, `rule_set_id`, `position`, `name`, `enabled`, `spec` (`jsonb`, a filter spec), timestamps |
| `muted_thread` | `id`, `user_id`, `account_id`, `message_ids` (`jsonb`, at most 200), `gm_thrid` (Gmail's thread id, or null), `created_at` |
| `filter_run` | `id`, `rule_set_id`, `trigger`, `created_at`, `started_at` (null while queued), `finished_at`, `dry_run`, `result` (`jsonb`, the filter engine's run report), `error` |

The per-account toggle is `account_filter_settings(user_id, account_id, run_filters bool default
false)`.

## API

Under `/api/v1`, signed-in user's own data only (another user's id is a 404).

| Route | Does |
| --- | --- |
| `GET /accounts/{id}/folders` | An account's folders, for the folder picker (`[{"path": [segments]}]`) |
| `GET`, `PUT /accounts/{id}/filter-settings` | Read, set *Run filters* (`{"run_filters": bool}`) |
| `GET /filters/schema` | The filter spec's JSON Schema (`json_schema()`), without `in_thread` |
| `POST /filters/draft-from-search` | A [draft](#filter-drafts) from a search's body: `{"spec", "dropped"}` |
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
  expression's complaint; `400` `hidden_criterion` for a spec naming `in_thread`;
- `400` `invalid_rule_set` (an `interval_minutes` exactly when `scheduled`), `invalid_order`;
- creating a rule set with the `to_sort` marker, or changing one to it: `400`
  `to_sort_unsupported` where the account's server keeps no keywords, and the mailbox failures
  of the account's folder listing when it cannot be asked;
- adding a filter or changing its spec: `400` `public_provider_domain` for a `from_domain` on a
  [public mail provider](#filter-drafts);
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
