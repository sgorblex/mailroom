import { z } from 'zod'
import { apiGetJson, apiPutJson } from './http.ts'

/** As the API checks it (`backend/mailroom/config/instance_settings.py`). */
export const MAX_RETENTION_DAYS = 3650

export const SESSION_IP_CHOICES = ['none', 'partial', 'full'] as const

/** What admins choose for the whole instance. */
export const instanceSettingsSchema = z.object({
  audit_retention_days: z.number(),
  session_ip: z.enum(SESSION_IP_CHOICES),
  external_sign_in_only: z.boolean(),
})
export type InstanceSettings = z.infer<typeof instanceSettingsSchema>

/**
 * `external_sign_in`: whether this admin may turn `external_sign_in_only` on.
 * `password_sign_in_forced`: the server's environment keeps passwords working whatever it says.
 */
export const instanceSettingsAnswerSchema = instanceSettingsSchema.extend({
  external_sign_in: z.enum(['available', 'not_configured', 'not_linked']),
  password_sign_in_forced: z.boolean(),
})
export type InstanceSettingsAnswer = z.infer<typeof instanceSettingsAnswerSchema>

const path = '/api/v1/admin/settings'

/** Admins only, like everything in this module. */
export function getInstanceSettings(signal?: AbortSignal): Promise<InstanceSettingsAnswer> {
  return apiGetJson(path, { init: { signal }, schema: instanceSettingsAnswerSchema })
}

/** `changes`: only the settings to change, so as not to undo what another admin changed meanwhile. */
export function changeInstanceSettings(
  changes: Partial<InstanceSettings>,
): Promise<InstanceSettingsAnswer> {
  return apiPutJson(path, changes, { schema: instanceSettingsAnswerSchema })
}
