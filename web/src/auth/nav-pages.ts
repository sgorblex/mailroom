import { Activity, AtSign, House, ListFilter, Mail, UserRound, Users, type LucideIcon } from 'lucide-react'

import type { Feature } from '@/api/auth.ts'
import { WRITES_MAIL } from '@/auth/feature-route.tsx'

export type NavPath = '/' | '/mail' | '/accounts' | '/filters' | '/activity' | '/users' | '/profile'

export interface NavPage {
  to: NavPath
  /** Its name's key in the `common` namespace. */
  label: string
  icon: LucideIcon
  /**
   * Where the app shell offers it: `tab` in the header's nav and as a tab on phones, `nav` in the
   * header's nav and under More on phones, `user` in the user menu.
   */
  place: 'tab' | 'nav' | 'user'
}

/** The pages the app shell (and the mail screen's command palette) offers: only the instance's features, Users only to an admin. */
export function navPages(features: ReadonlySet<Feature>, isAdmin: boolean): NavPage[] {
  const pages: (NavPage | false)[] = [
    { to: '/', label: 'nav.home', icon: House, place: 'tab' },
    features.has('mail') && { to: '/mail', label: 'nav.mail', icon: Mail, place: 'tab' },
    { to: '/accounts', label: 'nav.accounts', icon: AtSign, place: 'nav' },
    features.has('filters') && { to: '/filters', label: 'nav.filters', icon: ListFilter, place: 'tab' },
    WRITES_MAIL.some((feature) => features.has(feature)) && { to: '/activity', label: 'nav.activity', icon: Activity, place: 'nav' },
    isAdmin && { to: '/users', label: 'nav.users', icon: Users, place: 'nav' },
    { to: '/profile', label: 'nav.profile', icon: UserRound, place: 'user' },
  ]
  return pages.filter((page) => page !== false)
}
