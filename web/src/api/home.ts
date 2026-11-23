import { z } from 'zod'

import { ACCOUNT_COLOURS, accountIdSchema } from './accounts.ts'
import { apiGetJson } from './http.ts'

const inboxSchema = z.object({
  account_id: accountIdSchema,
  display_name: z.string(),
  colour: z.enum(ACCOUNT_COLOURS).nullable().catch(null),
  /** `null` while the index holds no readable copy of the inbox. */
  unread: z.number().nullable(),
})
export type HomeInbox = z.infer<typeof inboxSchema>

const mailSummarySchema = z.object({
  inboxes: z.array(inboxSchema),
  /** All inboxes: the sum of the counts known. */
  unread: z.number(),
  starred: z.number(),
  to_sort: z.number(),
  snoozed: z.number(),
  /** Snoozed messages coming back before the user's midnight. */
  snoozed_today: z.number(),
  scheduled: z.number(),
  next_scheduled_at: z.string().nullable(),
  scheduled_failed: z.number(),
})
export type MailSummary = z.infer<typeof mailSummarySchema>

const ruleSetGlanceSchema = z.object({
  id: z.number(),
  name: z.string(),
  /** `null`: a rule set for INBOX of every account. */
  account_id: accountIdSchema.nullable(),
  enabled: z.boolean(),
  disabled_reason: z.string().nullable(),
  /** Recent real runs (finished in the last 7 days, among those kept), and those that failed. */
  runs: z.number(),
  failed_runs: z.number(),
  last_run_at: z.string().nullable(),
  last_run_failed: z.boolean(),
  /** Messages its filters acted on in the last 7 days. */
  acted_on: z.number(),
})
export type RuleSetGlance = z.infer<typeof ruleSetGlanceSchema>

/** The Home page's summary; `mail` and `filters` are `null` where the instance does not run them. */
export const homeSummarySchema = z.object({
  mail: mailSummarySchema.nullable(),
  filters: z.object({ rule_sets: z.array(ruleSetGlanceSchema) }).nullable(),
})
export type HomeSummary = z.infer<typeof homeSummarySchema>

export function fetchHome(signal?: AbortSignal): Promise<HomeSummary> {
  return apiGetJson('/api/v1/home', { init: { signal }, schema: homeSummarySchema })
}
