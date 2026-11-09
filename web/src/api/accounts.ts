import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson, apiPutJson } from './http.ts'

export const TLS_MODES = ['implicit', 'starttls'] as const
export const SEND_MODES = ['off', 'drafts', 'approval', 'direct'] as const

const endpointSchema = z.object({
  host: z.string(),
  port: z.number(),
  tls: z.enum(TLS_MODES),
})
export type Endpoint = z.infer<typeof endpointSchema>

const identitySchema = z.object({
  address: z.string(),
  name: z.string().nullable().optional(),
  default: z.boolean().optional(),
})
export type Identity = z.infer<typeof identitySchema>

/** An account as the API lists it: never its password, only whether one is stored. */
export const accountSchema = z.object({
  id: z.string(),
  login: z.string(),
  send_mode: z.enum(SEND_MODES),
  imap: endpointSchema,
  smtp: endpointSchema,
  identities: z.array(identitySchema),
  auto_approve: z.array(z.string()),
  has_password: z.boolean(),
  /** Absent where the instance does not run filters. */
  run_filters: z.boolean().optional(),
  /** The user's limit on a draft's attachments, at most 25; absent from an API older than slice C. */
  attachment_max_mb: z.number().optional(),
  /** The user's limit on To, Cc and Bcc together; absent from an API older than slice C. */
  max_recipients: z.number().optional(),
})
export type Account = z.infer<typeof accountSchema>

/** What an edit sends: everything but the id; no password keeps the stored one. */
export interface AccountFields {
  login: string
  send_mode: (typeof SEND_MODES)[number]
  imap: Endpoint
  smtp: Endpoint
  identities: Identity[]
  auto_approve: string[]
  password?: string
}

export const loginTestSchema = z.object({
  ok: z.boolean(),
  code: z.string().nullable(),
  message: z.string().nullable(),
})
export type LoginTest = z.infer<typeof loginTestSchema>

/** A field mailroom-core found wrong: `path` inside the account (`["imap", "port"]`). */
export interface AccountIssue {
  path: (string | number)[]
  message: string
}

const issuesSchema = z.object({
  detail: z.object({
    issues: z.array(z.object({ path: z.array(z.union([z.string(), z.number()])), message: z.string() })),
  }),
})

/** The per-field problems of a refused add or edit, if the refusal carried any. */
export function accountIssues(body: unknown): AccountIssue[] {
  const parsed = issuesSchema.safeParse(body)
  return parsed.success ? parsed.data.detail.issues : []
}

const base = '/api/v1/accounts'
const one = (id: string): string => `${base}/${encodeURIComponent(id)}`

export function listAccounts(signal?: AbortSignal): Promise<Account[]> {
  return apiGetJson(base, { init: { signal }, schema: z.array(accountSchema) })
}

export function addAccount(account: AccountFields & { id: string; password: string }): Promise<Account> {
  return apiPostJson(base, account, { schema: accountSchema })
}

export function changeAccount(id: string, fields: AccountFields): Promise<Account> {
  return apiPutJson(one(id), fields, { schema: accountSchema })
}

export function removeAccount(id: string): Promise<null> {
  return apiDelete(one(id))
}

/** Logs in to IMAP and out again; a refused login comes back as `ok: false`. */
export function testAccount(id: string): Promise<LoginTest> {
  return apiPostJson(`${one(id)}/test`, undefined, { schema: loginTestSchema })
}
