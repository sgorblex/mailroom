import type { Feature } from '@/api/auth.ts'
import { WRITES_MAIL } from '@/auth/feature-route.tsx'

export type NavPath = '/' | '/mail' | '/accounts' | '/filters' | '/activity' | '/users' | '/profile'

export interface NavPage {
  to: NavPath
  /** Its name's key in the `common` namespace. */
  label: string
}

/** The pages the nav (and the mail screen's command palette) offers: only the instance's features, Users only to an admin. */
export function navPages(features: ReadonlySet<Feature>, isAdmin: boolean): NavPage[] {
  const pages: (NavPage | false)[] = [
    { to: '/', label: 'nav.home' },
    features.has('mail') && { to: '/mail', label: 'nav.mail' },
    { to: '/accounts', label: 'nav.accounts' },
    features.has('filters') && { to: '/filters', label: 'nav.filters' },
    WRITES_MAIL.some((feature) => features.has(feature)) && { to: '/activity', label: 'nav.activity' },
    isAdmin && { to: '/users', label: 'nav.users' },
    { to: '/profile', label: 'nav.profile' },
  ]
  return pages.filter((page) => page !== false)
}
