---
name: start-work
description: Use at the very start of a new task in this repository — before the first edit — to land in the right checkout on a correctly named Graphite branch. Covers choosing between the shared main checkout and a worktree, why `gt create` cannot open the first branch in a fresh agent worktree, the `git switch -c` + `gt track` pair that can, disposing of the scratch branch, and how much of a stack to name up front.
---

# Start work (branch and checkout setup)

Run this **before the first edit**, not after. Getting the branch right afterwards means rewriting history you have already built on.

## 1. Where am I

```bash
pwd                    # under .claude/worktrees/ ?
git branch --show-current
```

- **Inside `.claude/worktrees/<something>`** — the harness placed you there; that worktree is yours. Continue below. Do **not** `git worktree remove` it when you finish, and do not `cd` out to the main checkout.
- **In the shared main checkout** — feature-sized work belongs in its own worktree, but creating one is the user's call: **ask** before `git worktree add .claude/worktrees/<short_name>`. A small self-contained edit can stay where it is; that is the exception, not the default.

## 2. Pick the parent

```bash
gt log short    # or `gt ls`
```

Read it before creating anything, so the new branch stacks where you intend and you do not orphan, clobber, or silently restack someone else's branches. The parent is `main` for independent work, or the tip of the stack this builds on.

## 3. Open the branch

A harness-generated **`worktree-…`** branch is **untracked by Graphite**, and `gt` refuses every operation on it — `gt create` included, with `Cannot perform this operation on untracked branch`. So the first branch cannot be opened with `gt`:

```bash
git switch -c <type>/<short_name>
gt track --parent main            # or the branch you are stacking on
```

From there `gt create` works normally for every branch above it. The same pair also rescues work you have **already committed** onto the scratch branch — switching carries the commits with you, and `gt track` adopts them.

**`<type>/<short_name>`** — one slash, lowercase `snake_case`, two to four words, `<type>` being the Conventional Commit type the branch will squash to, no scope segment (`feat/available_mentors`, `fix/flag_scope_crash`). Full rules: [`ci-cd.md`](../../../docs/requirements/ci-cd.md) — *Git workflow and commit conventions*.

## 4. Dispose of the scratch branch

```bash
git branch -d worktree-…
```

Self-guarding: it refuses while the branch still holds commits of its own, and while any live worktree has it checked out. If it refuses, look — something is on it that you moved away from.

## 5. Name the stack you can see

If the work has an agreed plan, create and name the branches that plan calls for now, so each one is opened with its purpose already decided rather than carved out afterwards. **Adding branches later, as the work reveals them, is expected** — a stack that grew is not a planning failure. The shape gets judged at review time, against what the change turned out to be: see [`review-stack`](../review-stack/SKILL.md) — *Granularity*.

## Never

**No `git push`, `gt submit`, `gt push`, `gh pr create`, merge, or PR comment** — creating and tracking branches is local work; publishing them is not. Only the user's explicit request in that same turn changes that; see [`AGENTS.md`](../../../AGENTS.md) — *Before you implement*.
