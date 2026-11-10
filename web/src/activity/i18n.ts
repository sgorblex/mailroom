import i18n from '@/i18n/i18n.ts'
import en from '@/locales/en/activity.json'
import it from '@/locales/it/activity.json'

/**
 * The `activity` namespace, added when the Activity screen loads, so its strings travel in the
 * screen's own chunk; keys it lacks fall back to `common`.
 */
export const ACTIVITY_NS = 'activity'

i18n.addResourceBundle('en', ACTIVITY_NS, en)
i18n.addResourceBundle('it', ACTIVITY_NS, it)
