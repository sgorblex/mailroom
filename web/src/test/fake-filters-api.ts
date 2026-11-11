import { vi, type Mock } from 'vitest'

import filterSchema from './filter-schema.json'

type Body = Record<string, unknown>

export interface FakeAccount {
  id: string
  run_filters: boolean
}

export interface FakeRuleSet {
  id: number
  account_id: string
  folder: string[]
  name: string
  trigger: 'new_mail' | 'scheduled' | 'manual'
  interval_minutes: number | null
  starred_only: boolean
  enabled: boolean
}

export interface FakeFilter {
  id: number
  rule_set_id: number
  position: number
  name: string
  enabled: boolean
  spec: Body
}

export interface FakeRun {
  id: number
  rule_set_id: number
  trigger: string
  dry_run: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  result: Body | null
  error: string | null
}

export interface FakeState {
  accounts: FakeAccount[]
  folders: string[][]
  ruleSets: FakeRuleSet[]
  filters: FakeFilter[]
  runs: FakeRun[]
  /** What every enabled filter matches in a dry run. */
  messages: { uid: number; from: string | null; subject: string | null; date: string | null }[]
  /** A dry run looks at this many newest messages only. */
  limitedToNewest?: number
  /** Requests (`"<METHOD> <path>"`) answered with this status and body instead. */
  refuse?: Record<string, { status: number; body: unknown }>
  /** Requests (`"<METHOD> <path>"`) answered only once this settles. */
  gates?: Record<string, Promise<unknown>>
}

function account(id: string, runFilters: boolean): Body {
  const endpoint = { host: `imap.${id}.example`, port: 993, tls: 'implicit' }
  return {
    id,
    login: `me@${id}.example`,
    send_mode: 'drafts',
    imap: endpoint,
    smtp: { ...endpoint, host: `smtp.${id}.example`, port: 465 },
    identities: [{ address: `me@${id}.example`, name: null, default: true }],
    auto_approve: [],
    has_password: true,
    run_filters: runFilters,
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

const notFound = (): Response => json(404, { detail: 'Not Found' })
const invalidSpecResponse = (): Response =>
  json(400, { detail: { code: 'invalid_spec', message: "unknown criterion type 'bogus'" } })

/**
 * A fake filters API over `state`, as `docs/requirements/filters.md` specifies it. A spec whose
 * criterion type is `bogus` is refused as the filter engine would; Run now is refused while the
 * account's Run filters is off, and queues a run the worker never starts.
 */
export function stubFiltersApi(state: FakeState): Mock {
  let nextId = 1000
  const respond = (path: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET'
    const body: unknown = init?.body == null ? undefined : JSON.parse(init.body as string)
    const fields = (body ?? {}) as Body
    const withFilters = (ruleSet: FakeRuleSet): Body => ({
      ...ruleSet,
      filters: state.filters
        .filter((filter) => filter.rule_set_id === ruleSet.id)
        .sort((a, b) => a.position - b.position),
    })
    const invalidSpec = (spec: unknown): boolean =>
      JSON.stringify(spec).includes('"bogus"')

    if (path === '/api/v1/accounts' && method === 'GET') {
      return Promise.resolve(json(200, state.accounts.map((item) => account(item.id, item.run_filters))))
    }
    if (path === '/api/v1/filters/schema') {
      return Promise.resolve(json(200, filterSchema))
    }
    let match = /^\/api\/v1\/accounts\/([^/]+)\/(folders|rule-sets)$/.exec(path)
    if (match !== null) {
      const [, accountId, what] = match
      if (what === 'folders') {
        return Promise.resolve(json(200, state.folders.map((folder) => ({ path: folder }))))
      }
      if (method === 'GET') {
        return Promise.resolve(json(200, state.ruleSets.filter((item) => item.account_id === accountId)))
      }
      const created = { ...(fields as unknown as FakeRuleSet), id: (nextId += 1), account_id: accountId ?? '' }
      state.ruleSets.push(created)
      return Promise.resolve(json(201, created))
    }
    match = /^\/api\/v1\/rule-sets\/(\d+)(\/[a-z-]+)?$/.exec(path)
    if (match !== null) {
      const ruleSet = state.ruleSets.find((item) => item.id === Number(match?.[1]))
      if (ruleSet === undefined) {
        return Promise.resolve(notFound())
      }
      const rest = match[2] ?? ''
      if (rest === '' && method === 'GET') {
        return Promise.resolve(json(200, withFilters(ruleSet)))
      }
      if (rest === '' && method === 'PATCH') {
        Object.assign(ruleSet, fields)
        return Promise.resolve(json(200, ruleSet))
      }
      if (rest === '' && method === 'DELETE') {
        state.ruleSets = state.ruleSets.filter((item) => item !== ruleSet)
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      if (rest === '/filters' && method === 'POST') {
        if (invalidSpec(fields['spec'])) {
          return Promise.resolve(invalidSpecResponse())
        }
        const position = state.filters.filter((item) => item.rule_set_id === ruleSet.id).length
        const created = { ...(fields as unknown as FakeFilter), id: (nextId += 1), rule_set_id: ruleSet.id, position }
        state.filters.push(created)
        return Promise.resolve(json(201, created))
      }
      if (rest === '/order' && method === 'PUT') {
        ;(fields['filter_ids'] as number[]).forEach((id, position) => {
          const filter = state.filters.find((item) => item.id === id)
          if (filter !== undefined) {
            filter.position = position
          }
        })
        return Promise.resolve(json(200, withFilters(ruleSet)['filters']))
      }
      if (rest === '/dry-run' && method === 'POST') {
        const report = {
          account: ruleSet.account_id,
          folder: ruleSet.folder,
          dry_run: true,
          filters: (withFilters(ruleSet)['filters'] as FakeFilter[])
            .filter((filter) => filter.enabled)
            .map((filter) => ({
              filter_id: filter.id,
              spec: filter.spec,
              action: 'move_to',
              matched: state.messages,
              executed: false,
            })),
          error: null,
          limited_to_newest: state.limitedToNewest ?? null,
        }
        const run: FakeRun = {
          id: (nextId += 1),
          rule_set_id: ruleSet.id,
          trigger: 'manual',
          dry_run: true,
          created_at: '2026-10-20T10:00:00Z',
          started_at: '2026-10-20T10:00:00Z',
          finished_at: '2026-10-20T10:00:01Z',
          result: report,
          error: null,
        }
        state.runs.unshift(run)
        return Promise.resolve(json(200, run))
      }
      if (rest === '/run' && method === 'POST') {
        if (state.accounts.find((item) => item.id === ruleSet.account_id)?.run_filters !== true) {
          return Promise.resolve(
            json(400, { detail: { code: 'run_filters_off', message: 'Run filters is off for this account.' } }),
          )
        }
        const run: FakeRun = {
          id: (nextId += 1),
          rule_set_id: ruleSet.id,
          trigger: 'manual',
          dry_run: false,
          created_at: '2026-10-20T11:00:00Z',
          started_at: null,
          finished_at: null,
          result: null,
          error: null,
        }
        state.runs.unshift(run)
        return Promise.resolve(json(202, run))
      }
      if (rest === '/runs' && method === 'GET') {
        return Promise.resolve(json(200, state.runs.filter((run) => run.rule_set_id === ruleSet.id)))
      }
      return Promise.resolve(notFound())
    }
    match = /^\/api\/v1\/filters\/(\d+)$/.exec(path)
    if (match !== null) {
      const filterId = Number(match[1])
      const filter = state.filters.find((item) => item.id === filterId)
      if (filter === undefined) {
        return Promise.resolve(notFound())
      }
      if (method === 'PATCH') {
        if ('spec' in fields && invalidSpec(fields['spec'])) {
          return Promise.resolve(invalidSpecResponse())
        }
        Object.assign(filter, fields)
        return Promise.resolve(json(200, filter))
      }
      if (method === 'DELETE') {
        state.filters = state.filters.filter((item) => item !== filter)
        return Promise.resolve(new Response(null, { status: 204 }))
      }
    }
    return Promise.resolve(notFound())
  }
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const request = `${init?.method ?? 'GET'} ${path}`
    const refusal = state.refuse?.[request]
    if (refusal !== undefined) {
      return Promise.resolve(json(refusal.status, refusal.body))
    }
    const gate = state.gates?.[request]
    return gate === undefined ? respond(path, init) : gate.then(() => respond(path, init))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The JSON body of the last `method` request to `path`. */
export function sentBody(fetchMock: Mock, method: string, path: string): unknown {
  const call = fetchMock.mock.calls.findLast(
    ([callPath, init]) => callPath === path && (init as RequestInit | undefined)?.method === method,
  )
  if (call === undefined) {
    throw new Error(`no ${method} ${path}`)
  }
  return JSON.parse((call[1] as RequestInit).body as string)
}
