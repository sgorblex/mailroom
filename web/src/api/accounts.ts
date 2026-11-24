import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson, apiPutJson } from './http.ts'

export const TLS_MODES = ['implicit', 'starttls'] as const
export const SEND_MODES = ['off', 'drafts', 'approval', 'direct'] as const
export const ACCOUNT_COLOURS = ['slate', 'red', 'orange', 'amber', 'green', 'teal', 'sky', 'blue', 'violet', 'pink'] as const
export type AccountColour = (typeof ACCOUNT_COLOURS)[number]

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

/** An account's id: a number the server gives it, never shown to the user. */
export type AccountId = number
export const accountIdSchema = z.number().int().positive()

/** An account as the API lists it: never its password, only whether one is stored. */
export const accountSchema = z.object({
  id: accountIdSchema,
  /** The account's own name, as stored: `null` when it has none. What an edit sends back. */
  name: z.string().nullable(),
  /** What to call the account: its name, else its login. Read-only: never sent back. */
  display_name: z.string(),
  colour: z.enum(ACCOUNT_COLOURS).nullable(),
  /** The authentication server whose Authentication-Results are believed; `null` when unset (none are). */
  authserv_id: z.string().nullable(),
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
  name: string | null
  colour: AccountColour | null
  authserv_id: string | null
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

/** A suggested server: `outside_domain` when the domain's own document or SRV names a host outside it. */
const suggestedEndpointSchema = endpointSchema.extend({ outside_domain: z.boolean() })
export type SuggestedEndpoint = z.infer<typeof suggestedEndpointSchema>

const suggestionSchema = z.object({
  source: z.enum(['autoconfig', 'srv', 'mx', 'ispdb', 'preset']),
  /** Where it was read: the document's URL, the DNS names; null for a preset. */
  origin: z.string().nullable(),
  provider: z.string().nullable(),
  /** False for a provider Mailroom cannot sign in to yet. */
  supported: z.boolean(),
  note: z.enum(['app_password', 'oauth_required']).nullable(),
  /** What the login is: the whole address, or the part before the `@`. */
  login: z.enum(['address', 'local_part']),
  imap: suggestedEndpointSchema,
  smtp: suggestedEndpointSchema,
  /** The provider's authentication server, where it publishes one fixed name. */
  authserv_id: z.string().nullable(),
})
export type Suggestion = z.infer<typeof suggestionSchema>

const autoconfigAnswerSchema = z.object({
  domain: z.string(),
  suggestion: suggestionSchema.nullable(),
})
export type AutoconfigAnswer = z.infer<typeof autoconfigAnswerSchema>

const base = '/api/v1/accounts'
const one = (id: AccountId): string => `${base}/${String(id)}`

export function listAccounts(signal?: AbortSignal): Promise<Account[]> {
  return apiGetJson(base, { init: { signal }, schema: z.array(accountSchema) })
}

/** Adds an account, which the server gives its id. */
export function addAccount(account: AccountFields & { password: string }): Promise<Account> {
  return apiPostJson(base, account, { schema: accountSchema })
}

export function changeAccount(id: AccountId, fields: AccountFields): Promise<Account> {
  return apiPutJson(one(id), fields, { schema: accountSchema })
}

export function removeAccount(id: AccountId): Promise<null> {
  return apiDelete(one(id))
}

/** Logs in to IMAP and out again; a refused login comes back as `ok: false`. */
export function testAccount(id: AccountId): Promise<LoginTest> {
  return apiPostJson(`${one(id)}/test`, undefined, { schema: loginTestSchema })
}

/** The servers suggested for `address` (`docs/requirements/mail-client.md`, *Account autoconfiguration*). */
export function findSettings(address: string, signal?: AbortSignal): Promise<AutoconfigAnswer> {
  const query = new URLSearchParams({ address })
  return apiGetJson(`${base}/autoconfig?${query.toString()}`, { init: { signal }, schema: autoconfigAnswerSchema })
}

const accountKeywordsSchema = z.object({
  account_id: accountIdSchema,
  /** Whether the server keeps keywords, so messages can be marked To sort; `null` when it did not say. */
  keywords: z.boolean().nullable(),
})
export type AccountKeywords = z.infer<typeof accountKeywordsSchema>

/**
 * Whether each account's server keeps keywords, asked of the servers (a few seconds at most): apart
 * from the listing, which logs in to none of them.
 */
export function listAccountKeywords(signal?: AbortSignal): Promise<AccountKeywords[]> {
  return apiGetJson(`${base}/keywords`, { init: { signal }, schema: z.array(accountKeywordsSchema) })
}

const usageSchema = z.object({ used: z.number(), limit: z.number() })
export type QuotaUsage = z.infer<typeof usageSchema>

/** Both null when the server keeps no quota or limits neither. */
const accountQuotaSchema = z.object({
  account_id: accountIdSchema,
  /** In bytes. */
  storage: usageSchema.nullable(),
  messages: usageSchema.nullable(),
})
export type AccountQuota = z.infer<typeof accountQuotaSchema>

/** The quota the account's INBOX falls under (`docs/requirements/mail-client.md`, *Mailbox quota*). */
export function accountQuota(id: AccountId, signal?: AbortSignal): Promise<AccountQuota> {
  return apiGetJson(`${one(id)}/quota`, { init: { signal }, schema: accountQuotaSchema })
}

const identitySignatureSchema = z.object({
  address: z.string(),
  /** Sanitised as an outgoing message's HTML is, without images; empty for none. */
  html: z.string(),
  /** `html` as a message's text part has it; empty for none. */
  text: z.string(),
})
export type IdentitySignature = z.infer<typeof identitySignatureSchema>

/** Each of the account's identities, in its order, with its signature. */
export function accountSignatures(id: AccountId, signal?: AbortSignal): Promise<IdentitySignature[]> {
  return apiGetJson(`${one(id)}/signatures`, { init: { signal }, schema: z.array(identitySignatureSchema) })
}

/** Keeps the identity's signature as the server sanitises it; one without text is removed. */
export function saveSignature(id: AccountId, address: string, html: string): Promise<IdentitySignature> {
  return apiPutJson(`${one(id)}/signatures`, { address, html }, { schema: identitySignatureSchema })
}

const vacationReplySchema = z.object({
  enabled: z.boolean(),
  /** The first and last day it answers (`YYYY-MM-DD`, the user's time zone); null: open. */
  starts_on: z.string().nullable(),
  ends_on: z.string().nullable(),
  subject: z.string(),
  /** Sanitised as an outgoing message's HTML is, without images. */
  html: z.string(),
  text: z.string(),
  /** The identity it goes from; null: the one each message was addressed to. */
  identity: z.string().nullable(),
  /** On, and today is one of its days. */
  answering: z.boolean(),
  /** New mail reaches it: INBOX (on Gmail, All Mail) is on the mail index and synced. */
  indexed: z.boolean(),
})
export type VacationReply = z.infer<typeof vacationReplySchema>
export type VacationChange = Omit<VacationReply, 'text' | 'answering' | 'indexed'>

/** The account's vacation auto-reply (`docs/requirements/mail-client.md`, *Vacation reply*). */
export function accountVacation(id: AccountId, signal?: AbortSignal): Promise<VacationReply> {
  return apiGetJson(`${one(id)}/vacation`, { init: { signal }, schema: vacationReplySchema })
}

/** Keeps the account's vacation reply, its HTML as the server sanitises it. */
export function saveVacation(id: AccountId, change: VacationChange): Promise<VacationReply> {
  return apiPutJson(`${one(id)}/vacation`, change, { schema: vacationReplySchema })
}
