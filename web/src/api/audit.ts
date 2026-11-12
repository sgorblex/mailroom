import { z } from 'zod'
import { apiGetJson } from './http.ts'

export const ACTORS = ['user', 'agent', 'filter'] as const
export type Actor = (typeof ACTORS)[number]

/** The actions the web and MCP record; a filter's record names its own action instead. */
export const MAIL_ACTIONS = [
  'move',
  'archive',
  'trash',
  'flag',
  'draft_save',
  'draft_remove',
  'send',
  'folder_create',
  'folder_rename',
  'folder_delete',
] as const

/** One message a write acted on, or tried to: never its subject or body. */
export const auditRecordSchema = z.object({
  id: z.number(),
  at: z.string(),
  actor: z.string(),
  /** The connected app's name (or id), for an agent's record. */
  app: z.string().nullable(),
  client_id: z.string().nullable(),
  /** The rule set's name while it exists, for a filter's record. */
  rule_set: z.string().nullable(),
  rule_set_id: z.number().nullable(),
  run_id: z.number().nullable(),
  action: z.string(),
  account_id: z.string(),
  result: z.string(),
  detail: z.record(z.string(), z.unknown()),
})
export type AuditRecord = z.infer<typeof auditRecordSchema>

const pageSchema = z.object({ records: z.array(auditRecordSchema), next_cursor: z.string().nullable() })
export type AuditPage = z.infer<typeof pageSchema>

export interface AuditFilters {
  account_id?: string
  action?: string
  actor?: Actor
  /** `ok`, `failed` (anything but ok) or an error code. */
  result?: string
}

export function listAudit(filters: AuditFilters, cursor: string | null, signal?: AbortSignal): Promise<AuditPage> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (typeof value === 'string' && value !== '') {
      params.set(key, value)
    }
  }
  if (cursor !== null) {
    params.set('cursor', cursor)
  }
  const query = params.toString()
  return apiGetJson(`/api/v1/me/audit${query === '' ? '' : `?${query}`}`, { init: { signal }, schema: pageSchema })
}
