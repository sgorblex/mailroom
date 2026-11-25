import { getApiBaseUrl } from './api-base-url.ts'

/** A download of the user's rule sets with their filters, as JSON. */
export function ruleSetsExportUrl(): string {
  return `${getApiBaseUrl()}/api/v1/me/export/rule-sets`
}

/** A download of the user's audit trail as CSV, of the days from `since` to `until` (`YYYY-MM-DD`, '' for no bound). */
export function auditExportUrl(since: string, until: string): string {
  const params = new URLSearchParams()
  if (since !== '') {
    params.set('since', since)
  }
  if (until !== '') {
    params.set('until', until)
  }
  const query = params.toString()
  return `${getApiBaseUrl()}/api/v1/me/export/audit.csv${query === '' ? '' : `?${query}`}`
}
