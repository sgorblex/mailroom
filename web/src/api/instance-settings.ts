import { z } from 'zod'
import { apiGetJson, apiPutJson } from './http.ts'

/** As the API checks it (`backend/mailroom/config/instance_settings.py`). */
export const MAX_RETENTION_DAYS = 3650

export const SESSION_IP_CHOICES = ['none', 'partial', 'full'] as const

/** What admins choose for the whole instance. */
export const instanceSettingsSchema = z.object({
  audit_retention_days: z.number(),
  session_ip: z.enum(SESSION_IP_CHOICES),
})
export type InstanceSettings = z.infer<typeof instanceSettingsSchema>

const path = '/api/v1/admin/settings'

/** Admins only, like everything in this module. */
export function getInstanceSettings(signal?: AbortSignal): Promise<InstanceSettings> {
  return apiGetJson(path, { init: { signal }, schema: instanceSettingsSchema })
}

/** `changes`: only the settings to change, so as not to undo what another admin changed meanwhile. */
export function changeInstanceSettings(
  changes: Partial<InstanceSettings>,
): Promise<InstanceSettings> {
  return apiPutJson(path, changes, { schema: instanceSettingsSchema })
}
