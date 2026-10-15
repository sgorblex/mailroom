const DEFAULT_DEV_API_BASE_URL = 'http://127.0.0.1:8100'

export function getApiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL
  if (raw === undefined) {
    // Unset: same-origin /api so Vite dev proxy (or production gateway) keeps session cookies same-site.
    return ''
  }
  if (typeof raw !== 'string') {
    return DEFAULT_DEV_API_BASE_URL
  }
  const trimmed = raw.trim()
  if (trimmed === '') {
    if (raw === '') {
      return ''
    }
    return DEFAULT_DEV_API_BASE_URL
  }
  return trimmed.replace(/\/$/, '')
}
