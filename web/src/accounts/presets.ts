import type { Suggestion } from '@/api/accounts.ts'

type Server = Omit<Suggestion['imap'], 'outside_domain'>

/** Mailroom chose these hosts, so none is outside the domain. */
function preset(provider: string, imap: Server, smtp: Server): Suggestion {
  return {
    source: 'preset',
    origin: null,
    provider,
    supported: true,
    note: 'app_password',
    login: 'address',
    imap: { ...imap, outside_domain: false },
    smtp: { ...smtp, outside_domain: false },
  }
}

/**
 * The providers offered when a lookup finds nothing: the supported presets of the API's own
 * (`PROVIDERS` in `backend/mailroom/accounts/autoconfig.py`), which answer only for their domains.
 */
export const PRESETS: readonly Suggestion[] = [
  preset('Gmail', { host: 'imap.gmail.com', port: 993, tls: 'implicit' }, { host: 'smtp.gmail.com', port: 465, tls: 'implicit' }),
  preset(
    'iCloud',
    { host: 'imap.mail.me.com', port: 993, tls: 'implicit' },
    { host: 'smtp.mail.me.com', port: 587, tls: 'starttls' },
  ),
  preset(
    'Fastmail',
    { host: 'imap.fastmail.com', port: 993, tls: 'implicit' },
    { host: 'smtp.fastmail.com', port: 465, tls: 'implicit' },
  ),
  preset(
    'Yahoo',
    { host: 'imap.mail.yahoo.com', port: 993, tls: 'implicit' },
    { host: 'smtp.mail.yahoo.com', port: 465, tls: 'implicit' },
  ),
]
