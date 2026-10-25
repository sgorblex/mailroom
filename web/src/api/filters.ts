import { z } from 'zod'
import { apiDelete, apiGetJson, apiPatchJson, apiPostJson, apiPutJson } from './http.ts'

export const TRIGGERS = ['new_mail', 'scheduled', 'manual'] as const
export type Trigger = (typeof TRIGGERS)[number]

/**
 * An imap-thingy filter spec (`{"criterion": …, "action": …}`), kept as the server sent it: the
 * server validates it, and the editor reads it with `@/filters/spec.ts`.
 */
const specSchema = z.record(z.string(), z.unknown())

export const filterSchema = z.object({
  id: z.number(),
  rule_set_id: z.number(),
  position: z.number(),
  name: z.string(),
  enabled: z.boolean(),
  spec: specSchema,
})
export type Filter = z.infer<typeof filterSchema>

export const ruleSetSchema = z.object({
  id: z.number(),
  account_id: z.string(),
  folder: z.array(z.string()),
  name: z.string(),
  trigger: z.enum(TRIGGERS),
  interval_minutes: z.number().nullable().optional(),
  starred_only: z.boolean(),
  enabled: z.boolean(),
})
export type RuleSet = z.infer<typeof ruleSetSchema>

const ruleSetWithFiltersSchema = ruleSetSchema.extend({ filters: z.array(filterSchema) })
export type RuleSetWithFilters = z.infer<typeof ruleSetWithFiltersSchema>

/** What a rule set is made or edited with; `interval_minutes` only for a `scheduled` one. */
export interface RuleSetFields {
  name: string
  folder: string[]
  trigger: Trigger
  interval_minutes: number | null
  starred_only: boolean
  enabled: boolean
}

export interface FilterFields {
  name: string
  enabled: boolean
  spec: Record<string, unknown>
}

/** A message a filter matched, as imap-thingy summarises it. Mail content: shown as text only. */
const matchedMessageSchema = z.object({
  uid: z.number(),
  from: z.string().nullable(),
  subject: z.string().nullable(),
  date: z.string().nullable(),
})
export type MatchedMessage = z.infer<typeof matchedMessageSchema>

const filterReportSchema = z.object({
  /** The filter this entry is about. */
  filter_id: z.number(),
  spec: specSchema.nullable(),
  action: z.string(),
  matched: z.array(matchedMessageSchema),
  /** How many matched, when a run's report keeps only the first ones in `matched`. */
  matched_total: z.number().optional(),
  executed: z.boolean(),
})
export type FilterReport = z.infer<typeof filterReportSchema>

/** imap-thingy's report of one folder run: per filter that ran, what it matched and did. */
const runReportSchema = z.object({
  folder: z.array(z.string()),
  dry_run: z.boolean(),
  filters: z.array(filterReportSchema),
  error: z.string().nullable().optional(),
  /** A dry run looks at this many newest messages when the folder holds more. */
  limited_to_newest: z.number().nullable().optional(),
})
export type RunReport = z.infer<typeof runReportSchema>

const runSchema = z.object({
  id: z.number(),
  rule_set_id: z.number(),
  trigger: z.string(),
  dry_run: z.boolean(),
  created_at: z.string(),
  /** `null` while queued. */
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  result: runReportSchema.nullable(),
  error: z.string().nullable(),
})
export type Run = z.infer<typeof runSchema>

const ruleSet = (id: number): string => `/api/v1/rule-sets/${String(id)}`
const accountRuleSets = (accountId: string): string =>
  `/api/v1/accounts/${encodeURIComponent(accountId)}/rule-sets`

/** imap-thingy's JSON Schema of a filter spec: the editor builds its fields from it. */
export function getFilterSchema(signal?: AbortSignal): Promise<unknown> {
  return apiGetJson('/api/v1/filters/schema', { init: { signal } })
}

export function listRuleSets(accountId: string, signal?: AbortSignal): Promise<RuleSet[]> {
  return apiGetJson(accountRuleSets(accountId), { init: { signal }, schema: z.array(ruleSetSchema) })
}

export function addRuleSet(accountId: string, fields: RuleSetFields): Promise<RuleSet> {
  return apiPostJson(accountRuleSets(accountId), fields, { schema: ruleSetSchema })
}

export function getRuleSet(id: number, signal?: AbortSignal): Promise<RuleSetWithFilters> {
  return apiGetJson(ruleSet(id), { init: { signal }, schema: ruleSetWithFiltersSchema })
}

export function changeRuleSet(id: number, fields: Partial<RuleSetFields>): Promise<RuleSet> {
  return apiPatchJson(ruleSet(id), fields, { schema: ruleSetSchema })
}

export function removeRuleSet(id: number): Promise<null> {
  return apiDelete(ruleSet(id))
}

/** Adds a filter at the end of the rule set. */
export function addFilter(ruleSetId: number, fields: FilterFields): Promise<Filter> {
  return apiPostJson(`${ruleSet(ruleSetId)}/filters`, fields, { schema: filterSchema })
}

export function changeFilter(id: number, fields: Partial<FilterFields>): Promise<Filter> {
  return apiPatchJson(`/api/v1/filters/${String(id)}`, fields, { schema: filterSchema })
}

export function removeFilter(id: number): Promise<null> {
  return apiDelete(`/api/v1/filters/${String(id)}`)
}

/** Puts the rule set's filters in the order of `filterIds` (all of them). */
export async function reorderFilters(ruleSetId: number, filterIds: number[]): Promise<void> {
  await apiPutJson<unknown>(`${ruleSet(ruleSetId)}/order`, { filter_ids: filterIds })
}

/** Evaluates the rule set against the folder's mail, changing nothing; the run joins the history. */
export function dryRun(ruleSetId: number): Promise<Run> {
  return apiPostJson(`${ruleSet(ruleSetId)}/dry-run`, undefined, { schema: runSchema })
}

/** Queues a run for the worker. */
export function runNow(ruleSetId: number): Promise<Run> {
  return apiPostJson(`${ruleSet(ruleSetId)}/run`, undefined, { schema: runSchema })
}

/** The rule set's runs, newest first. */
export function listRuns(ruleSetId: number, signal?: AbortSignal): Promise<Run[]> {
  return apiGetJson(`${ruleSet(ruleSetId)}/runs`, { init: { signal }, schema: z.array(runSchema) })
}
