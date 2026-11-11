# Documentation

Product documentation for **Mailroom**, a self-hosted mail app for a few separate users.

**Where to start:** [`CONTEXT.md`](CONTEXT.md), then only the [`REQUIREMENTS.md`](REQUIREMENTS.md)
section you are changing. Workflow and *which* doc to update after a change:
[`../AGENTS.md`](../AGENTS.md) — *After you change the repository*.

## Read in this order

| # | Path | Role |
| --- | --- | --- |
| 1 | [`CONTEXT.md`](CONTEXT.md) | What Mailroom is, the component family, audience, non-goals |
| 2 | [`REQUIREMENTS.md`](REQUIREMENTS.md) | Capabilities with maturity |
| 3 | [`GLOSSARY.md`](GLOSSARY.md) | Domain vocabulary |
| 4 | [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) | Stack, security, migrations, logging, testing, i18n |
| 5 | [`requirements/ci-cd.md`](requirements/ci-cd.md) | Git workflow, CI gates, SemVer, changelog |
| 6 | [`deploy.md`](deploy.md) | Containerfiles, image names, Quadlet sketch |

## Reference

| Path | Role |
| --- | --- |
| [`requirements/filters.md`](requirements/filters.md) | Filters: rule sets, triggers, dry run, data model, API |
| [`requirements/mail-client.md`](requirements/mail-client.md) | Mail client: slices, screens, REST contract |
| [`requirements/mcp.md`](requirements/mcp.md) | MCP server: tools, OAuth modes, web SSO, endpoints, config |
| [`core.md`](core.md) | mailroom-core (`backend/core`): store, render, read, organize, compose, drafts, send gate, filters, safety |
| [`TODO.md`](TODO.md) | Next phases, roughly in order — not committed requirements |
| [`IDEAS.md`](IDEAS.md) | Exploratory ideas — not scoped, not scheduled |
| [`../deploy/quadlet/README.md`](../deploy/quadlet/README.md) | Quadlet units (sketch of the deployed ones) |

## Conventions

**Maturity:** **Planned** → **Current** → **Partial** → **Done**, defined in
[`REQUIREMENTS.md`](REQUIREMENTS.md). **Headings carry no numbers**: refer to a section by name.
**Define a term once** in [`GLOSSARY.md`](GLOSSARY.md) and link to it. A new feature area gets its
own `requirements/<kebab-topic>.md` plus a short linking section in `REQUIREMENTS.md`.
