# Filters

The filters feature: [rule sets](../GLOSSARY.md) of imap-thingy filters per account folder, edited
in the browser, tried with a dry run, and run by the worker. Product scope:
[`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Filters UI*.

## Behaviour

- **Run filters**, per account, is **off by default**. Until the user turns it on, the worker runs
  nothing for that account: no trigger fires and *Run now* is refused. Editing and dry runs work
  either way.
- A **rule set** belongs to one account and one folder, and has a **trigger**:
  - `new_mail` — runs when the folder gets new mail (IMAP IDLE);
  - `scheduled` — runs every `interval_minutes`;
  - `manual` — runs only on *Run now*.

  Any enabled rule set can also be run now. **Starred only** restricts a rule set to starred
  (`\Flagged`) messages, which it unstars once handled (imap-thingy's `starred_only`).
- A rule set holds **ordered filters**, each an imap-thingy filter spec (criteria plus actions),
  stored as imap-thingy serialises it and validated with `imap_thingy.spec.load_filter`. Filters
  run in order; a disabled filter is skipped, by dry runs too. A disabled rule set never runs (no
  trigger fires, *Run now* is refused) but can still be dry-run; a new rule set starts disabled.
- **Dry run** evaluates the rule set against the folder's real mail and reports, per filter, the
  messages it matches (uid, from, subject, date) and what it would do, changing nothing (the folder
  is selected read-only). Each filter is evaluated against the folder as it is, without the
  earlier filters applied, so a dry run may over-report: a message an earlier filter would move
  can show up under a later one too.
- **Run history**: every run — on new mail, scheduled, run now or dry — records its trigger
  (`manual` for run now and dry runs), start and end, the report (what each filter matched and
  did) and any error.
- **One run at a time per account.** The worker never runs two runs on the same account at once;
  a run waits for the one in progress. Dry runs, which change nothing, are not held back.

## Data model

Schema `app`; every row belongs to the user who owns the account.

| Table | Columns |
| --- | --- |
| `rule_set` | `id`, `user_id`, `account_id`, `folder` (`text[]`, path segments), `name`, `trigger` (`new_mail` \| `scheduled` \| `manual`), `interval_minutes` (`scheduled` only), `starred_only`, `enabled`, timestamps |
| `filter` | `id`, `rule_set_id`, `position`, `name`, `enabled`, `spec` (`jsonb`, an imap-thingy filter spec), timestamps |
| `filter_run` | `id`, `rule_set_id`, `trigger`, `started_at`, `finished_at`, `dry_run`, `result` (`jsonb`, imap-thingy's run report), `error` |

The per-account toggle is `account_filter_settings(user_id, account_id, run_filters bool default
false)`.

## API

Under `/api/v1`, signed-in user's own data only (another user's id is a 404).

| Route | Does |
| --- | --- |
| `GET`, `PUT /accounts/{id}/filter-settings` | Read, set *Run filters* (`{"run_filters": bool}`) |
| `GET /filters/schema` | imap-thingy's filter spec JSON Schema (`json_schema()`) |
| `GET`, `POST /accounts/{id}/rule-sets` | List, create an account's rule sets |
| `GET`, `PATCH`, `DELETE /rule-sets/{id}` | Read (with its filters), edit, remove a rule set |
| `POST /rule-sets/{id}/filters` | Add a filter (at the end) |
| `PATCH`, `DELETE /filters/{id}` | Edit, remove a filter |
| `PUT /rule-sets/{id}/order` | Reorder: the rule set's filter ids, in the new order |
| `POST /rule-sets/{id}/dry-run` | Dry run, synchronous and bounded; returns the report |
| `POST /rule-sets/{id}/run` | Run now: enqueues a run, `202` with the run id |
| `GET /rule-sets/{id}/runs` | Run history, newest first |

An invalid spec is a `400` `{"code": "invalid_spec", "message": …}` carrying imap-thingy's
`SpecError` text.
