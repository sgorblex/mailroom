import { vi, type Mock } from 'vitest'

import filterSchema from './filter-schema.json'

type Body = Record<string, unknown>

export interface FakeAccount {
  id: number
  run_filters: boolean
  /** Its own name: `ACCOUNT_NAMES`' unless it says otherwise, else unnamed and shown by its login. */
  name?: string
  colour?: string
  /** What it keeps Handled as; the star unless it says otherwise. */
  handled?: string
  /** Whether its server keeps keywords; `null` when not known. */
  keywords?: boolean | null
}

export interface FakeRuleSet {
  id: number
  /** `all` for a rule set for all accounts. */
  account_id: number | 'all'
  folder: string[]
  name: string
  trigger: 'new_mail' | 'scheduled' | 'manual'
  interval_minutes: number | null
  marker: 'none' | 'starred' | 'handled'
  enabled: boolean
}

export interface FakeFilter {
  id: number
  rule_set_id: number
  position: number
  name: string
  enabled: boolean
  spec: Body
  /** The last edit; a fixed date unless it says otherwise, and the time of an edit through the fake. */
  updated_at?: string
  /** None unless it says otherwise. */
  matches_30d?: number
}

/** When the fake's filters were last edited, unless they say otherwise. */
export const FILTER_EDITED = '2026-11-12T12:00:00Z'

function filterOut(filter: FakeFilter): Body {
  return { updated_at: FILTER_EDITED, matches_30d: 0, ...filter }
}

export interface FakeRun {
  id: number
  rule_set_id: number
  /** Its rule set's account unless it says otherwise. */
  account_id?: number
  trigger: string
  dry_run: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  result: Body | null
  error: string | null
}

/** A run as the API answers it: on its rule set's account unless it names one. */
const runOut =
  (ruleSet: FakeRuleSet) =>
  (run: FakeRun): Body => ({ account_id: ruleSet.account_id === 'all' ? 0 : ruleSet.account_id, ...run })

export interface FakeState {
  accounts: FakeAccount[]
  folders: string[][]
  /** An account's own folders, where they are not `folders`. */
  accountFolders?: Record<number, string[][]>
  ruleSets: FakeRuleSet[]
  filters: FakeFilter[]
  runs: FakeRun[]
  /** What every enabled filter matches in a dry run. */
  messages: { uid: number; from: string | null; subject: string | null; date: string | null }[]
  /** A dry run looks at this many newest messages only. */
  limitedToNewest?: number
  /** What `GET /mail/messages/{ref}/filter-draft` answers, by ref. */
  messageDrafts?: Record<string, Body>
  /** What `POST /filters/draft-from-search` answers. */
  searchDraft?: Body
  /** What `GET /mail/muted` lists, each as the API answers it. */
  muted?: Body[]
  /** Requests (`"<METHOD> <path>"`) answered with this status and body instead. */
  refuse?: Record<string, { status: number; body: unknown }>
  /** Requests (`"<METHOD> <path>"`) answered only once this settles. */
  gates?: Record<string, Promise<unknown>>
  /** When an edit through the fake happens: now unless it says otherwise. */
  editedAt?: string
}

/** The accounts the tests use, and the names the fake lists them under. */
export const WORK = 1
export const HOME = 2
const ACCOUNT_NAMES: Record<number, string> = { [WORK]: 'work', [HOME]: 'home' }

function account({ id, run_filters: runFilters, name, colour, handled }: FakeAccount): Body {
  const endpoint = { host: `imap.${String(id)}.example`, port: 993, tls: 'implicit' }
  return {
    id,
    name: name ?? ACCOUNT_NAMES[id] ?? null,
    display_name: name ?? ACCOUNT_NAMES[id] ?? `me@${String(id)}.example`,
    colour: colour ?? null,
    authserv_id: null,
    login: `me@${String(id)}.example`,
    send_mode: 'drafts',
    imap: endpoint,
    smtp: { ...endpoint, host: `smtp.${String(id)}.example`, port: 465 },
    identities: [{ address: `me@${String(id)}.example`, name: null, default: true }],
    auto_approve: [],
    has_password: true,
    run_filters: runFilters,
    handled: handled ?? 'star',
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
 * account's Run filters is off, and queues a run the worker never starts (one per running account
 * for a rule set for all accounts, whose dry run needs `?account_id=`).
 */
export function stubFiltersApi(state: FakeState): Mock {
  let nextId = 1000
  const respond = (url: string, init?: RequestInit): Promise<Response> => {
    const [path = url, query = ''] = url.split('?')
    const queryAccount = new URLSearchParams(query).get('account_id')
    const method = init?.method ?? 'GET'
    const body: unknown = init?.body == null ? undefined : JSON.parse(init.body as string)
    const fields = (body ?? {}) as Body
    const withFilters = (ruleSet: FakeRuleSet): Body => ({
      ...ruleSet,
      filters: state.filters
        .filter((filter) => filter.rule_set_id === ruleSet.id)
        .sort((a, b) => a.position - b.position)
        .map(filterOut),
    })
    const invalidSpec = (spec: unknown): boolean =>
      JSON.stringify(spec).includes('"bogus"')

    if (path === '/api/v1/accounts' && method === 'GET') {
      return Promise.resolve(json(200, state.accounts.map(account)))
    }
    if (path === '/api/v1/accounts/keywords' && method === 'GET') {
      return Promise.resolve(json(200, state.accounts.map(({ id, keywords }) => ({ account_id: id, keywords: keywords ?? null }))))
    }
    if (path === '/api/v1/filters/schema') {
      return Promise.resolve(json(200, filterSchema))
    }
    if (path === '/api/v1/filters/draft-from-search' && method === 'POST' && state.searchDraft !== undefined) {
      return Promise.resolve(json(200, state.searchDraft))
    }
    if (path === '/api/v1/mail/muted' && method === 'GET') {
      return Promise.resolve(json(200, { threads: state.muted ?? [] }))
    }
    let match = /^\/api\/v1\/mail\/muted\/(\d+)$/.exec(path)
    if (match !== null && method === 'DELETE') {
      const before = state.muted ?? []
      state.muted = before.filter((thread) => thread['id'] !== Number(match?.[1]))
      return Promise.resolve(state.muted.length === before.length ? notFound() : new Response(null, { status: 204 }))
    }
    match = /^\/api\/v1\/mail\/messages\/([^/]+)\/filter-draft$/.exec(path)
    if (match !== null) {
      const draft = state.messageDrafts?.[decodeURIComponent(match[1] ?? '')]
      return Promise.resolve(draft === undefined ? notFound() : json(200, draft))
    }
    match = /^\/api\/v1\/accounts\/([^/]+)\/(folders|rule-sets)$/.exec(path)
    if (match !== null) {
      const [, id, what] = match
      const accountId: FakeRuleSet['account_id'] = id === 'all' ? 'all' : Number(id)
      if (what === 'folders') {
        if (accountId !== 'all' && !state.accounts.some((account) => account.id === accountId)) {
          return Promise.resolve(notFound())
        }
        const folders = (accountId === 'all' ? undefined : state.accountFolders?.[accountId]) ?? state.folders
        return Promise.resolve(json(200, folders.map((folder) => ({ path: folder }))))
      }
      if (method === 'GET') {
        return Promise.resolve(json(200, state.ruleSets.filter((item) => item.account_id === accountId)))
      }
      const created = { ...(fields as unknown as FakeRuleSet), id: (nextId += 1), account_id: accountId }
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
        return Promise.resolve(json(201, filterOut(created)))
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
        const runAccount = ruleSet.account_id === 'all' ? Number(queryAccount) : ruleSet.account_id
        if (ruleSet.account_id === 'all' && queryAccount === null) {
          return Promise.resolve(
            json(400, { detail: { code: 'account_required', message: 'Name the account.' } }),
          )
        }
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
          account_id: runAccount,
          trigger: 'manual',
          dry_run: true,
          created_at: '2026-10-20T10:00:00Z',
          started_at: '2026-10-20T10:00:00Z',
          finished_at: '2026-10-20T10:00:01Z',
          result: report,
          error: null,
        }
        state.runs.unshift(run)
        return Promise.resolve(json(200, runOut(ruleSet)(run)))
      }
      if (rest === '/run' && method === 'POST') {
        const running = state.accounts
          .filter((item) => item.run_filters && (ruleSet.account_id === 'all' || item.id === ruleSet.account_id))
          .map((item) => item.id)
        if (running.length === 0) {
          return Promise.resolve(
            json(400, { detail: { code: 'run_filters_off', message: 'Run filters is off for this account.' } }),
          )
        }
        const queued = running.map(
          (accountId): FakeRun => ({
            id: (nextId += 1),
            rule_set_id: ruleSet.id,
            account_id: accountId,
            trigger: 'manual',
            dry_run: false,
            created_at: '2026-10-20T11:00:00Z',
            started_at: null,
            finished_at: null,
            result: null,
            error: null,
          }),
        )
        state.runs.unshift(...queued)
        const answered = queued.map(runOut(ruleSet))
        // Only a rule set for all accounts answers a list; one account's answers its run.
        return Promise.resolve(json(202, ruleSet.account_id === 'all' ? answered : answered[0]))
      }
      if (rest === '/runs' && method === 'GET') {
        return Promise.resolve(
          json(200, state.runs.filter((run) => run.rule_set_id === ruleSet.id).map(runOut(ruleSet))),
        )
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
        // As the API: an edit that changes nothing leaves the date of the last one.
        const changed = Object.entries(fields).some(
          ([key, value]) => JSON.stringify(filter[key as keyof FakeFilter]) !== JSON.stringify(value),
        )
        Object.assign(filter, fields, changed ? { updated_at: state.editedAt ?? new Date().toISOString() } : {})
        return Promise.resolve(json(200, filterOut(filter)))
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
