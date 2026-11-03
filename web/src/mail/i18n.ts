import i18n from '@/i18n/i18n.ts'
import en from '@/locales/en/mail.json'
import it from '@/locales/it/mail.json'

/**
 * The `mail` namespace, added when the mail screens load, so their strings travel in the screens'
 * own chunk; keys it lacks fall back to `common`.
 */
export const MAIL_NS = 'mail'

i18n.addResourceBundle('en', MAIL_NS, en)
i18n.addResourceBundle('it', MAIL_NS, it)
