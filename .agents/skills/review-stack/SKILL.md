---
name: review-stack
description: Use before submitting a branch — or a whole Graphite stack — as PRs in this repository, or when asked to review the current branch's or stack's own changes for quality, architecture, naming, bugs, convention adherence, and scope, then apply the safe fixes. Covers per-branch diff scoping across a stack, parallel per-branch review, this repo's review dimensions and doc-update obligations, and the bottom-up mise verification sweep. Successor to `review-and-fix`.
---

# Review and fix a branch or stack (pre-PR pass)

Self-review of **the branches' own changes**, run once implementation feels done and before any PR exists. Works on one branch or an N-branch **Graphite** stack. Report first, then fix what is safe to fix.

## 1. Pick the branches

```bash
gt log short -sr    # this stack, trunk-first (-s = ancestors+descendants of HEAD, -r = reversed)
```

A harness-generated **`worktree-…`** branch is **untracked**, and every `gt` operation on it errors, `-s` included — move the work onto a real branch first (`git switch -c <type>/<short_name>`, then `gt track --parent main`; see [`AGENTS.md`](../../../AGENTS.md) — *Git worktrees*), or fall back to `gt log short -r` and pick the stack by hand. Ordered trunk → tip:

```bash
gt log short -sr | sed -E 's/\([^)]*\)//g; s/[^A-Za-z0-9._\/-]+/ /g' | awk 'NF{print $NF}'
```

Default target: every branch above `main` in that list. Stacks here run to a dozen branches — if only part of it is this session's work, say which branches you are reviewing and confirm with the user before sweeping the rest. A `(name)` annotation means that branch is checked out in **another worktree**: you can diff and read it, but you cannot check it out or amend it.

## 2. Scope each branch

"The diff" is against the branch's **parent**, not `main`. In a trunk-first list the parent is the previous entry (`main` for the first). No checkout needed — diff the refs:

```bash
git merge-base --is-ancestor "$PARENT" "$BRANCH"   # must pass; if not, wrong parent or unrestacked
git log --oneline "$PARENT..$BRANCH"               # this branch's own commits
git diff --stat "$PARENT...$BRANCH"                # blast radius
git diff "$PARENT...$BRANCH"                       # the review surface
git status --short                                 # uncommitted work (checked-out branch only)
```

Three-dot, so an unrestacked parent does not drag its own changes in — and if the ancestor check fails you have either paired the wrong parent (a fork in the flattened `-r` list) or the stack needs `gt restack` — resolve that before reviewing, or every finding from the diff is suspect. The working tree sits at **one** branch: read context for any other with **`git show <branch>:<path>`**, never from disk. Then read whatever the diff raises questions about — callers, an existing util that makes the new code redundant, the true size of a file the diff only touches.

## 3. Review dimensions

Per branch:

- **Bugs and functionality** — does it do what the branch claims, including edge cases and error paths.
- **Architecture** — right layer, right boundary, no logic leaking into transport or UI.
- **Reuse and convention** — an existing component, helper, or pattern already covers this.
- **Naming** — clarity over abbreviation; purely functional methods named as **nouns, not verbs**.
- **Readability** and **internal consistency**.
- **Restraint** — comments that restate the code (CI YAML especially), and documentation added beyond what the change requires.
- **Size** — a component or file that should be split or extracted further.
- **Doc obligations** — walk the *After you change the repository* table in [`AGENTS.md`](../../../AGENTS.md), row by row, against this branch's diff. It is the list; do not work from memory of it.
- **Security** — mail content is untrusted input: HTML mail only ever renders in the sandboxed iframe, no remote content by default, nothing from a message reaches a header, a log line in clear, or an action without the user's say-so. See [`nonfunctional-requirements.md`](../../../docs/requirements/nonfunctional-requirements.md) — *Security*.
- **Mail invariants** — for anything touching `backend/core` or calling it, check the change against [`AGENTS.md`](../../../AGENTS.md) — *Mail invariants*, point by point. A regression here outranks everything else, and logic touching these needs tests, refusal paths included.
- **Branch ships as one unit** — every commit here squash-merges into one commit on `main` ([`ci-cd.md`](../../../docs/requirements/ci-cd.md) — *Git workflow and commit conventions*). If an earlier commit in this **unmerged** branch added something a later commit makes obsolete, fix it **at the source** with `gt modify`, never with a follow-up revert commit.
- **Branch name and commits** — `<type>/<short_name>`, snake_case, no scope segment; Conventional Commits throughout; the type matches what the branch actually squashes to.
- **mise discipline** — new instructions, scripts, and docs route through **`mise run`** / **`mise exec`**, never bare `uv` / `pnpm` / `pytest` / `ruff` / `alembic`.

Across the stack (only visible from the whole sweep — do this pass yourself, after the per-branch ones land):

- **Granularity** — judge the stack that actually resulted, not the one that was planned: branches get added along the way, and that is normal. The test is whether it reads as a **sequence of changes each worth reviewing on its own** — every branch squashing to one coherent commit that reverts on its own, nothing riding along that belongs in its own PR, and no branch so slight that reviewing it separately tells you less than reading it inside its neighbour. Where it does not, **`gt fold`** (into the parent, restacking descendants; **`--close`** if it has an open PR) or **`gt split --by-commit`** / **`--by-file <pathspec>`**. Expect to fold more often than you split — a stack drifts toward too many branches far more easily than too few — but that is a prior, not a quota, and neither number is the point.
- **Churn** — a later branch rewriting, renaming, or deleting what an earlier one added. Fix it at the source in the earlier branch when both are unmerged and unsubmitted; **flag instead of amending** when the earlier branch already has an open PR.
- **Migrations** — one Alembic head, `down_revision` chain in stack order, and the round-trip still passes at the tip.
- **Duplication** — the same helper, type, i18n key, or fixture invented independently in two branches.
- **Doc and requirement attribution** — the doc row, glossary term, or `.env.example` entry lands in the branch that introduces the behaviour, not at the tip.
- **Flags and scaffolding** — a feature flag or shim added low in the stack and never removed by the branch that made it redundant.
- Anything else worth flagging.

## 4. Parallelise the reads, serialise the writes

Per-branch review is independent — for a stack of three or more, dispatch **one read-only subagent per branch** in a single message: give it the branch and parent refs, the per-branch dimension list above, and the rule that it may only run `git diff` / `git log` / `git show` and read files, never check out, edit, or run `gt`. Collect their findings, then do the cross-branch pass yourself. One or two branches: just read them inline.

Everything that writes — fixes, `gt modify`, verification — stays **serial**, because there is one working tree and one database.

## 5. Report, then triage

Report **before** touching anything: findings grouped by branch, then the cross-branch section, each as `file:line` plus one line of why, ordered by severity. Nothing worth raising is a result too — say so explicitly rather than going silent.

Then, per finding:

- **Easy, clearly an improvement, and in scope for the branch that owns it** → apply it.
- **Restructuring the stack** (a fold or a split) → **propose it, never apply it silently.** It rewrites branches and invalidates any open PR, so it needs the same explicit go-ahead as a design decision.
- **A real improvement but needs an architectural or design decision** → stop and ask.
- **Out of scope for the stack's topic** → do not propose it at all.

## 6. Apply the fixes

Bottom-up, one branch at a time:

```bash
gt checkout <branch>   # skip: branches another worktree holds
# apply that branch's fixes
git status --short     # nothing unrelated about to be staged
gt modify --all        # amends the branch commit; auto-restacks descendants
```

Amending is right rather than adding a commit — the branch squash-merges as one unit. If the tree held unrelated pre-existing changes, `git add` only the reviewed paths and run `gt modify` without `--all`. Finish with `gt log short -sr` to confirm nothing is left needing a restack.

Never `git stash` here: the stash stack is shared with every other worktree on this machine. Set work aside with a WIP commit instead.

## 7. Verify the sweep

Every checkout shares **one** dev database (`127.0.0.1:5434`, from `mise.toml`'s `[env]`): there is no per-worktree port registry. Start it once with `mise run dev-db` (or check it is already up and leave it running) before any test run.

Then, bottom-up, run only what each branch's paths touch:

| Changed | Run |
| --- | --- |
| anything | `mise run fix` |
| `backend/**` | `mise run check-backend`, `mise run test-backend` |
| a migration | `mise run check-migrations` |
| `web/**` | `mise run check-web-types` (seconds, the inner loop), then `mise run check-web`, `mise run test-web` |
| `scripts/lib/**` | `mise run check-scripts` |
| a version copy, `release-please-config.json` | `mise run check-version-sync` |

`mise run check-all` / `test-all` cover the lot when a branch touches most areas. Independent tasks can run concurrently; `test-backend` and `check-migrations` share the database, so serialise those two. Never typecheck web with `tsc -p tsconfig.json` — that solution file has `"files": []` and exits 0 having checked nothing.

Traps that cost a whole pass if missed:

- **Bottom-up is not optional.** The shared database keeps the highest Alembic revision any branch stamped it with; stepping down the stack then fails with `Can't locate revision identified by '<rev>'` — database state, not code.
- **Clear stale bytecode when moving down**: `find backend -name __pycache__ -type d -prune -exec rm -rf {} +`. A `.pyc` of a migration the older branch does not have reproduces that same error even against a clean database.
- **A schema change means dropping the test databases by hand.** `mise run reset-db` does not touch them, and `pytest -n auto` creates one per worker: drop `mailroom_test` **and** every `mailroom_gwN_test`, then recreate `mailroom_test` empty (the harness expects it to exist). **One `psql -c` per statement** — `DROP DATABASE` cannot run inside a transaction block. If `pg_stat_activity` shows anyone else connected, stop: another session is using them.
- **Stop only what you started.** If the database was already running when you began, leave it running.

Report a branch as verified only once its checks have **actually run**, and say plainly which ones did not.

## Never

**No `git push`, `gt submit`, `gt push`, `gh pr create`, merge, or PR comment** — this skill stops one step short of the PR. Only the user's explicit request in that same turn changes that; see [`AGENTS.md`](../../../AGENTS.md) — *Before you implement*.
