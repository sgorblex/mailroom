import { z } from 'zod'
import { apiGetJson } from './http.ts'

export const healthSchema = z.object({ status: z.literal('ok') })

export type Health = z.infer<typeof healthSchema>

export function fetchHealth(signal?: AbortSignal): Promise<Health> {
  return apiGetJson('/api/v1/health', { init: { signal }, schema: healthSchema })
}

export const versionSchema = z.object({
  version: z.string(),
  git_sha: z.string(),
  git_branch: z.string(),
  deploy_env: z.string(),
})

export type BuildVersion = z.infer<typeof versionSchema>

/** The API's build and the environment it runs in, read at runtime so one image serves both
 * staging and production. */
export function fetchVersion(signal?: AbortSignal): Promise<BuildVersion> {
  return apiGetJson('/api/v1/version', { init: { signal }, schema: versionSchema })
}
