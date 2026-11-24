import type { AccountId } from '@/api/accounts.ts'
import { ACTORS, MAIL_ACTIONS, type Actor, type AuditFilters, type AuditRecord } from '@/api/audit.ts'
import { accountId, text } from '@/mail/route-search.ts'

/**
 * One app, as the URL names it: a connected app's grant, a personal API token, or a client, for
 * one with no grant here (an external issuer's). Ids, so it names one since revoked too.
 */
export type AppKey = `grant:${string}` | `token:${string}` | `client:${string}`

/**
 * The Activity page's filters, kept in the URL so a link, a reload or Back lands on the same list.
 * Kept out of the screen's chunk: the router validates it before the screen loads.
 */
export interface ActivitySearch {
  account?: AccountId
  actor?: Actor
  action?: (typeof MAIL_ACTIONS)[number]
  result?: (typeof RESULTS)[number]
  app?: AppKey
}

export const RESULTS = ['ok', 'failed'] as const

/** The key's kind and id; `undefined` for what is none. */
function appParts(value: unknown): { kind: 'grant' | 'token' | 'client'; id: string } | undefined {
  const [, number, number_id, client] =
    typeof value === 'string' ? (/^(?:(grant|token):([1-9][0-9]{0,15})|client:([\x20-\x7e]{1,2048}))$/.exec(value) ?? []) : []
  if (client !== undefined) {
    return { kind: 'client', id: client }
  }
  return number_id !== undefined && Number.isSafeInteger(Number(number_id))
    ? { kind: number === 'grant' ? 'grant' : 'token', id: number_id }
    : undefined
}

export function appKey(value: unknown): AppKey | undefined {
  return appParts(value) === undefined ? undefined : (value as AppKey)
}

/** The key of the app an agent's record came through; `undefined` when it names none. */
export function recordApp(record: AuditRecord): AppKey | undefined {
  const { grant_id, token_id, client_id } = record
  const grant = grant_id === null ? undefined : appKey(`grant:${grant_id}`)
  const token = token_id === null ? undefined : appKey(`token:${String(token_id)}`)
  return grant ?? token ?? (client_id === null ? undefined : appKey(`client:${client_id}`))
}

export function validateActivitySearch(raw: Record<string, unknown>): ActivitySearch {
  const search: ActivitySearch = {}
  const account = accountId(raw['account'])
  const actor = ACTORS.find((each) => each === raw['actor'])
  const action = MAIL_ACTIONS.find((each) => each === raw['action'])
  const result = RESULTS.find((each) => each === raw['result'])
  const app = appKey(text(raw['app']))
  if (account !== undefined) {
    search.account = account
  }
  if (actor !== undefined) {
    search.actor = actor
  }
  if (action !== undefined) {
    search.action = action
  }
  if (result !== undefined) {
    search.result = result
  }
  if (app !== undefined) {
    search.app = app
  }
  return search
}

/** The search as the API's filters. */
export function auditFilters({ account, app, ...rest }: ActivitySearch): AuditFilters {
  const filters: AuditFilters = { ...rest }
  if (account !== undefined) {
    filters.account_id = account
  }
  const parts = appParts(app)
  if (parts?.kind === 'token') {
    filters.token_id = Number(parts.id)
  } else if (parts?.kind === 'grant') {
    filters.grant_id = parts.id
  } else if (parts?.kind === 'client') {
    filters.client_id = parts.id
  }
  return filters
}
