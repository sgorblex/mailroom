---
name: review-and-fix
description: Use before submitting a branch as a PR in this repository, or when asked to review the current branch's own changes — quality, architecture, naming, bugs, convention adherence, scope — and apply the safe fixes. Covers Graphite-accurate diff scoping, this repo's review dimensions and doc-update obligations, and the mise checks to re-run afterwards.
---

# Review and fix (pre-PR pass)

Self-review of **this branch's own changes**, run once the implementation feels done and before the PR exists. Report first, then fix what is safe to fix.

## 1. Scope the branch

**Graphite** stacking means "the diff" is against the branch's **parent**, not `main` — `gt parent` errors on trunk, so fall back:

```bash
gt log short                       # where this branch sits in the stack
BASE=$(gt parent 2>/dev/null || echo main)
git log --oneline "$BASE..HEAD"    # this branch's own commits
git diff --stat "$BASE...HEAD"     # blast radius
git status --short                 # uncommitted work in the tree
git diff "$BASE...HEAD"            # the review surface
```

Three-dot, so an unrestacked parent does not drag its own changes in. Then read whatever the diff raises questions about — callers, an existing util that makes the new code redundant, the true size of a file the diff only touches.

## 2. Review dimensions

- **Bugs and functionality** — does it do what the branch claims, including edge cases and error paths.
- **Architecture** — right layer, right boundary, no logic leaking into transport or UI.
- **Reuse and convention** — an existing component, helper, or pattern already covers this.
- **Naming** — clarity over abbreviation; purely functional methods named as **nouns, not verbs**.
- **Readability** and **internal consistency** across the branch.
- **Restraint** — comments that restate the code (CI YAML especially), and documentation added beyond what the change requires.
- **Size** — a component or file that should be split or extracted further.
- **Scope and grouping** — do the changes belong in one PR, or are two unrelated features riding together.
- **Doc obligations** — walk the *After you change the repository* table in [`AGENTS.md`](../../../AGENTS.md), row by row, against this diff. It is the list; do not work from memory of it.
- **Security** — mail content is untrusted input: HTML mail only ever renders in the sandboxed iframe, no remote content by default, nothing from a message reaches a header, a log line in clear, or an action without the owner's say-so. See [`nonfunctional-requirements.md`](../../../docs/requirements/nonfunctional-requirements.md) — *Security*.
- **Branch ships as one unit** — every commit here squash-merges into one commit on `main` ([`ci-cd.md`](../../../docs/requirements/ci-cd.md) — *Git workflow and commit conventions*). If an earlier commit in this **unmerged** branch added something a later commit makes obsolete, fix it **at the source** with `gt modify`, never with a follow-up revert commit.
- **mise discipline** — new instructions, scripts, and docs route through **`mise run`** / **`mise exec`**, never bare `uv` / `pnpm` / `pytest` / `ruff` / `alembic`.
- Anything else worth flagging.

## 3. Report, then triage

Report **before** touching anything: findings grouped by severity, each as `file:line` plus one line of why. Nothing worth raising is a result too — say so explicitly rather than going silent.

Then, per finding:

- **Easy, clearly an improvement, and in scope for this branch** → apply it.
- **A real improvement but needs an architectural or design decision** → stop and ask.
- **Out of scope for the branch's topic** → do not propose it at all.

## 4. Verify what changed

```bash
mise run fix                                     # always
mise run check-backend && mise run test-backend  # if backend/** changed
mise run check-web     && mise run test-web      # if web/** changed
```

`test-backend` needs `mise run dev-db` running first. A local `.env` beats the ambient environment (mise's `[env] _.file`), so exporting a variable on the command line will not override it.

## 5. Fold the fixes in

Only once verification passes:

```bash
git status --short   # nothing unrelated about to be staged
gt modify --all      # amends the branch commit; auto-restacks descendants
```

Amending is right rather than adding a commit — the branch squash-merges as one unit. If the tree held unrelated pre-existing changes, `git add` only the reviewed paths and run `gt modify` without `--all`.

## Never

**No `git push`, `gt submit`, `gt push`, `gh pr create`, merge, or PR comment** — this skill stops one step short of the PR. Only the user's explicit request in that same turn changes that; see [`AGENTS.md`](../../../AGENTS.md) — *Before you implement*.
