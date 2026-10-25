import i18n from '@/i18n/i18n.ts'
import en from '@/locales/en/filters.json'
import it from '@/locales/it/filters.json'

/**
 * The `filters` namespace, added when the filter screens load, so their strings travel in the
 * screens' own chunk; keys it lacks fall back to `common`.
 */
export const FILTERS_NS = 'filters'

i18n.addResourceBundle('en', FILTERS_NS, en)
i18n.addResourceBundle('it', FILTERS_NS, it)
